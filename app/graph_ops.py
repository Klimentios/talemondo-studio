"""Structural edits of any ComfyUI API graph, checked against the live ComfyUI /object_info.

One engine for every lane that changes a graph: «Свои воркфлоу» (local_studio/user_workflows.py), edits of an
H3 job's graph (local_studio/routes/jobs.py) and the agent tools. It knows nothing about a particular node pack:
node classes, input types, ranges, choice lists (installed LoRAs, samplers…) and output types all come from
/object_info, so a third-party pack installed later is edited the same way.

Operations (a list, applied in order; `$name` refers to a node added earlier in the same list):
  set           {node, input, value}               literal value of a widget input (checked type, range, choices)
  link          {node, input, from: [node, slot]}   wire an input to another node's output (types must match)
  unset         {node, input}                       drop an optional input
  add_node      {class_type, inputs?, title?, ref?} new node; widget inputs not given get the schema default
  remove_node   {node, bypass?}                     delete; bypass=true rewires its consumers to its own input
                                                    of the same type (removing a LoRA keeps the model chain)
  replace_node  {node, class_type, inputs?}         new class in place: kept inputs that the new class has,
                                                    consumers stay wired if the output types still match
  add_lora      {lora_name, strength_model?, strength_clip?, after?}  LoraLoaderModelOnly (or LoraLoader when
                                                    strength_clip is given) inserted after the MODEL source `after`
                                                    (default: the only model loader of the graph)
  title         {node, title}                       the node's display title (_meta.title)

Any model may be used: turbo / distilled / quantized weights are only noted in `warnings`.
The result is validated as a whole (scripts/h3_graph_validate.validate + cycles); problems the graph already had
are reported apart from the ones this edit introduced, so a graph that waits for a pack can still be edited.
"""
from __future__ import annotations

import copy
import re

from .object_info import combo_options

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
MAX_OPS = 200
MODEL_LOADERS = ("UNETLoader", "CheckpointLoaderSimple", "UnetLoaderGGUF", "DiffusionModelLoader", "ImageOnlyCheckpointLoader")
# Fast weights (turbo / distilled / few-step / quantized) are allowed; the edit only notes them in `warnings`.
FAST_WEIGHTS = re.compile(r"turbo|lightning|lightx2v|distill|hyper[-_]?sd|lcm|causvid|[-_.]int8|[-_.]fp8|[-_.]nf4|"
                          r"\.gguf$|[-_.]q[2-8](_k|_0|_1)?\b|[-_]\d{1,2}steps?\b", re.I)
OPS = ("set", "link", "unset", "add_node", "remove_node", "replace_node", "add_lora", "title")


class GraphEditError(ValueError):
    pass


def is_link(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int) \
        and not isinstance(value[1], bool)


def node_schema(info: dict, class_type: str) -> dict:
    if class_type not in info:
        raise GraphEditError(f"В ComfyUI нет узла {class_type} (проверь «Узлы и модели»)")
    return info[class_type]


def input_spec(info: dict, class_type: str, name: str):
    inputs = (info.get(class_type) or {}).get("input") or {}
    return (inputs.get("required") or {}).get(name, (inputs.get("optional") or {}).get(name))


def spec_type(spec) -> str | None:
    """'INT', 'MODEL', 'COMBO'… of an input spec; a legacy combo ([choices], {...}) is 'COMBO'."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], list):
        return "COMBO"
    return spec[0] if isinstance(spec[0], str) else None


def spec_limits(spec) -> dict:
    return spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}


def outputs(info: dict, class_type: str) -> list:
    out = (info.get(class_type) or {}).get("output")
    return out if isinstance(out, list) else []


def _types_match(expected, actual) -> bool:
    if not isinstance(expected, str) or not isinstance(actual, str) or "*" in (expected, actual):
        return True
    # ComfyUI allows "IMAGE,MASK"-style unions on some custom nodes
    return actual in expected.split(",") or expected in actual.split(",")


def check_literal(info: dict, node_id: str, class_type: str, name: str, value):
    """The value ComfyUI will accept for a widget input, or GraphEditError with the reason in Russian."""
    where = f"{class_type} #{node_id} · {name}"
    spec = input_spec(info, class_type, name)
    if spec is None:
        raise GraphEditError(f"{class_type} #{node_id}: входа «{name}» нет в схеме узла")
    kind = spec_type(spec)
    if isinstance(value, (list, dict)):
        raise GraphEditError(f"{where}: значение должно быть простым (связь — операция link)")
    if kind not in WIDGET_TYPES:
        raise GraphEditError(f"{where}: это вход-связь типа {kind}, его задаёт link")
    options = combo_options(spec)
    if kind == "COMBO":
        if options and value not in options:
            raise GraphEditError(f"{where}: «{value}» не из списка {options[:12]}")
        return value
    limits = spec_limits(spec)
    if kind in ("INT", "FLOAT"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "INT" and int(value) != value):
            raise GraphEditError(f"{where}: нужно число {kind}")
        if ("min" in limits and value < limits["min"]) or ("max" in limits and value > limits["max"]):
            raise GraphEditError(f"{where}: вне {limits.get('min')}…{limits.get('max')}")
        step, base = limits.get("step"), limits.get("min", 0)
        # INT steps carry meaning on video nodes (width/height ×32, H3 length 5 + 17k frames); ComfyUI does not check them
        if kind == "INT" and isinstance(step, int) and step > 1 and isinstance(base, int) and (int(value) - base) % step:
            raise GraphEditError(f"{where}: шаг {step} от {base} (например {base + (int(value) - base) // step * step})")
        return int(value) if kind == "INT" else float(value)
    if kind == "STRING" and not isinstance(value, str) or kind == "BOOLEAN" and not isinstance(value, bool):
        raise GraphEditError(f"{where}: нужен тип {kind}")
    return value


def default_value(spec):
    """Schema default of a widget input; None when the input is a link or has no sensible default."""
    kind = spec_type(spec)
    if kind not in WIDGET_TYPES:
        return None
    limits = spec_limits(spec)
    if "default" in limits:
        return limits["default"]
    options = combo_options(spec)
    if options:
        return options[0]
    return {"INT": limits.get("min", 0), "FLOAT": float(limits.get("min", 0.0)), "STRING": "", "BOOLEAN": False}.get(kind)


def _next_id(graph: dict) -> str:
    numbers = [int(key) for key in graph if key.isdigit()]
    return str(max(numbers, default=0) + 1)


def _consumers(graph: dict, node_id: str) -> list[tuple[str, str, int]]:
    return [(key, name, value[1]) for key, node in graph.items() for name, value in (node.get("inputs") or {}).items()
            if is_link(value) and value[0] == node_id]


def _has_cycle(graph: dict) -> list[str]:
    state: dict[str, int] = {}

    def visit(key: str, trail: list[str]) -> list[str]:
        state[key] = 1
        for value in (graph[key].get("inputs") or {}).values():
            if is_link(value) and value[0] in graph:
                if state.get(value[0]) == 1:
                    return trail + [value[0]]
                if not state.get(value[0]):
                    found = visit(value[0], trail + [value[0]])
                    if found:
                        return found
        state[key] = 2
        return []

    for key in graph:
        if not state.get(key):
            found = visit(key, [key])
            if found:
                return found
    return []


def graph_problems(graph: dict, info: dict) -> list[str]:
    """Every problem ComfyUI would report on POST /prompt, without running it."""
    from scripts.h3_graph_validate import validate

    problems = validate(graph, info)
    for key, node in graph.items():
        for name, value in (node.get("inputs") or {}).items():
            if is_link(value) and value[0] not in graph and not any(f"[{key}]" in p and name in p for p in problems):
                problems.append(f"[{key}] {node.get('class_type')}: вход {name} ссылается на отсутствующий узел {value[0]}")
    cycle = _has_cycle(graph)
    if cycle:
        problems.append("Цикл в графе: " + " → ".join(cycle))
    return problems


class _Editor:
    def __init__(self, graph: dict, info: dict) -> None:
        self.graph = copy.deepcopy(graph)
        self.info = info
        self.refs: dict[str, str] = {}
        self.notes: list[str] = []
        self.last = ""

    def node_id(self, value) -> str:
        key = str(value)
        if key.startswith("$"):
            if key[1:] not in self.refs:
                raise GraphEditError(f"Ссылка {key}: такой узел не добавлялся в этой правке")
            return self.refs[key[1:]]
        if key not in self.graph:
            raise GraphEditError(f"Узла {key} нет в графе")
        return key

    def source(self, value) -> list:
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            raise GraphEditError("from: нужен [узел, номер выхода]")
        key, slot = self.node_id(value[0]), value[1]
        if not isinstance(slot, int) or isinstance(slot, bool):
            raise GraphEditError("from: номер выхода — целое число")
        outs = outputs(self.info, self.graph[key]["class_type"])
        if outs and not 0 <= slot < len(outs):
            raise GraphEditError(f"У {self.graph[key]['class_type']} #{key} нет выхода {slot} (выходы: {outs})")
        return [key, slot]

    def wire(self, key: str, name: str, link: list) -> None:
        node = self.graph[key]
        spec = input_spec(self.info, node["class_type"], name)
        if spec is None:
            raise GraphEditError(f"{node['class_type']} #{key}: входа «{name}» нет в схеме узла")
        expected = spec_type(spec)
        outs = outputs(self.info, self.graph[link[0]]["class_type"])
        actual = outs[link[1]] if link[1] < len(outs) else None
        if expected in WIDGET_TYPES - {"COMBO"} and actual is None:
            pass  # a primitive/converted widget fed by a link: ComfyUI accepts it, types unknown here
        elif actual is not None and not _types_match(expected, actual):
            raise GraphEditError(f"{node['class_type']} #{key} · {name}: ждёт {expected}, "
                                 f"а {self.graph[link[0]]['class_type']} #{link[0]} отдаёт {actual}")
        node.setdefault("inputs", {})[name] = link

    def apply(self, op: dict) -> None:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise GraphEditError(f"Неизвестная операция {op.get('op') if isinstance(op, dict) else op!r}; есть: {', '.join(OPS)}")
        getattr(self, "op_" + op["op"])(op)

    def op_set(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        node, name = self.graph[key], op.get("input")
        if not isinstance(name, str) or not name:
            raise GraphEditError("set: нужен input")
        if is_link(op.get("value")):
            raise GraphEditError(f"{node['class_type']} #{key} · {name}: связь задаётся операцией link")
        node.setdefault("inputs", {})[name] = check_literal(self.info, key, node["class_type"], name, op.get("value"))

    def op_link(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        name = op.get("input")
        if not isinstance(name, str) or not name:
            raise GraphEditError("link: нужен input")
        self.wire(key, name, self.source(op.get("from")))

    def op_unset(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        node, name = self.graph[key], op.get("input")
        required = ((self.info.get(node["class_type"]) or {}).get("input") or {}).get("required") or {}
        if name in required:
            raise GraphEditError(f"{node['class_type']} #{key} · {name}: обязательный вход нельзя убрать")
        (node.get("inputs") or {}).pop(name, None)

    def op_add_node(self, op: dict) -> None:
        class_type = op.get("class_type")
        schema = node_schema(self.info, class_type)
        key = str(op["id"]) if op.get("id") is not None else _next_id(self.graph)
        if key in self.graph or not re.fullmatch(r"[0-9A-Za-z_:.-]{1,40}", key):
            raise GraphEditError(f"Номер узла {key} занят или недопустим")
        self.graph[key] = {"class_type": class_type, "inputs": {}, "_meta": {"title": str(op.get("title") or
                                                                            schema.get("display_name") or class_type)}}
        ref = op.get("ref")
        if ref:
            self.refs[str(ref).lstrip("$")] = key
        given = op.get("inputs") or {}
        if not isinstance(given, dict):
            raise GraphEditError("add_node: inputs — объект {вход: значение}")
        for name, spec in ((schema.get("input") or {}).get("required") or {}).items():
            if name not in given:
                value = default_value(spec)
                if value is not None:
                    self.graph[key]["inputs"][name] = value
        for name, value in given.items():
            if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], int):
                self.wire(key, name, self.source(value))
            else:
                self.graph[key]["inputs"][name] = check_literal(self.info, key, class_type, name, value)
        self.last = key
        self.notes.append(f"добавлен {class_type} #{key}")

    def op_remove_node(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        node = self.graph[key]
        consumers = [c for c in _consumers(self.graph, key) if c[0] != key]
        if consumers and not op.get("bypass"):
            listed = ", ".join(f"{self.graph[c[0]]['class_type']} #{c[0]}.{c[1]}" for c in consumers[:6])
            raise GraphEditError(f"{node['class_type']} #{key} подключён к: {listed}. Передай bypass=true, чтобы "
                                 "провести связь мимо, или сначала перепривяжи входы")
        outs = outputs(self.info, node["class_type"])
        for consumer, name, slot in consumers:
            wanted = outs[slot] if slot < len(outs) else None
            through = next((value for value in (node.get("inputs") or {}).values() if is_link(value) and value[0] in self.graph
                            and (outputs(self.info, self.graph[value[0]]["class_type"]) or [None] * (value[1] + 1))[value[1]] == wanted),
                           None)
            if through is None:
                raise GraphEditError(f"{node['class_type']} #{key}: нечем заменить выход {wanted} для "
                                     f"{self.graph[consumer]['class_type']} #{consumer}.{name}")
            self.graph[consumer]["inputs"][name] = list(through)
        del self.graph[key]
        self.notes.append(f"удалён {node['class_type']} #{key}" + (" (связь проведена мимо)" if consumers else ""))

    def op_replace_node(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        old = self.graph[key]
        class_type = op.get("class_type")
        schema = node_schema(self.info, class_type)
        known = {**((schema.get("input") or {}).get("required") or {}), **((schema.get("input") or {}).get("optional") or {})}
        kept, dropped = {}, []
        for name, value in (old.get("inputs") or {}).items():
            if name not in known:
                dropped.append(name)
                continue
            if is_link(value):
                kept[name] = value
                continue
            try:
                kept[name] = check_literal(self.info, key, class_type, name, value)
            except GraphEditError:
                dropped.append(name)
        meta = dict(old.get("_meta") or {})
        if not op.get("keep_title"):
            meta["title"] = schema.get("display_name") or class_type
        self.graph[key] = {"class_type": class_type, "inputs": kept, "_meta": meta}
        for name, spec in ((schema.get("input") or {}).get("required") or {}).items():
            if name not in kept:
                value = default_value(spec)
                if value is not None:
                    self.graph[key]["inputs"][name] = value
        for name, value in (op.get("inputs") or {}).items():
            if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], int):
                self.wire(key, name, self.source(value))
            else:
                self.graph[key]["inputs"][name] = check_literal(self.info, key, class_type, name, value)
        for name in list(kept):
            if is_link(kept[name]):
                self.wire(key, name, kept[name])  # recheck the type against the new class
        new_outs = outputs(self.info, class_type)
        old_outs = outputs(self.info, old["class_type"])
        for consumer, name, slot in _consumers(self.graph, key):
            if slot >= len(new_outs) or (slot < len(old_outs) and not _types_match(old_outs[slot], new_outs[slot])):
                raise GraphEditError(f"{class_type} не отдаёт {old_outs[slot] if slot < len(old_outs) else '?'} на "
                                     f"выходе {slot}, а его ждёт {self.graph[consumer]['class_type']} #{consumer}.{name}")
        self.notes.append(f"{old['class_type']} #{key} → {class_type}" + (f" (сброшены входы: {', '.join(dropped)})" if dropped else ""))

    def op_add_lora(self, op: dict) -> None:
        with_clip = op.get("strength_clip") is not None
        class_type = "LoraLoader" if with_clip else "LoraLoaderModelOnly"
        node_schema(self.info, class_type)
        if op.get("after") is not None:
            source = self.node_id(op["after"])
        else:
            loaders = [key for key, node in self.graph.items() if node["class_type"] in MODEL_LOADERS]
            if len(loaders) != 1:
                raise GraphEditError(f"Укажи after: узел, после которого ставить LoRA (загрузчиков модели: {len(loaders)})")
            source = loaders[0]
        outs = outputs(self.info, self.graph[source]["class_type"])
        if "MODEL" not in outs:
            raise GraphEditError(f"{self.graph[source]['class_type']} #{source} не отдаёт MODEL")
        model_slot = outs.index("MODEL")
        inputs = {"model": [source, model_slot], "lora_name": op.get("lora_name"),
                  "strength_model": op.get("strength_model", 1.0)}
        if with_clip:
            clip_from = op.get("clip_from")
            if clip_from is None:
                clip_slot = outs.index("CLIP") if "CLIP" in outs else None
                clips = [key for key, node in self.graph.items() if "CLIP" in outputs(self.info, node["class_type"])
                         and node["class_type"] not in ("LoraLoader",)]
                if clip_slot is not None:
                    clip_from = [source, clip_slot]
                elif len(clips) == 1:
                    clip_from = [clips[0], outputs(self.info, self.graph[clips[0]]["class_type"]).index("CLIP")]
                else:
                    raise GraphEditError("LoRA с силой для CLIP: укажи clip_from [узел, выход]")
            inputs |= {"clip": clip_from, "strength_clip": op["strength_clip"]}
        consumers = [c for c in _consumers(self.graph, source) if c[2] == model_slot]
        clip_consumers = []
        if with_clip:
            clip_src = self.source(inputs["clip"])
            clip_consumers = [c for c in _consumers(self.graph, clip_src[0]) if c[2] == clip_src[1]]
        self.op_add_node({"class_type": class_type, "inputs": inputs, "ref": op.get("ref"),
                          "title": op.get("title") or f"LoRA · {op.get('lora_name')}"})
        new = self.last
        for consumer, name, _ in consumers:
            if consumer != new:
                self.graph[consumer]["inputs"][name] = [new, 0]
        for consumer, name, _ in clip_consumers:
            if consumer != new:
                self.graph[consumer]["inputs"][name] = [new, 1]

    def op_title(self, op: dict) -> None:
        key = self.node_id(op.get("node"))
        self.graph[key].setdefault("_meta", {})["title"] = str(op.get("title") or "")[:120]


def apply_ops(graph: dict, ops: list, info: dict) -> dict:
    """{graph, changes, notes, problems, new_problems}. GraphEditError if an operation itself is impossible."""
    from .graph_edit import graph_changes

    if not isinstance(ops, list) or not ops:
        raise GraphEditError("Нет операций: передай ops=[{op, …}]")
    if len(ops) > MAX_OPS:
        raise GraphEditError(f"Больше {MAX_OPS} операций за раз")
    editor = _Editor(graph, info)
    for index, op in enumerate(ops, 1):
        try:
            editor.apply(op)
        except GraphEditError as exc:
            raise GraphEditError(f"Операция {index} ({op.get('op') if isinstance(op, dict) else '?'}): {exc}") from exc
    before = set(graph_problems(graph, info))
    problems = graph_problems(editor.graph, info)
    return {"graph": editor.graph, "changes": graph_changes(graph, editor.graph), "notes": editor.notes,
            "warnings": quality_problems(graph, editor.graph),
            "refs": editor.refs, "problems": problems, "new_problems": [p for p in problems if p not in before]}


def quality_problems(before: dict, after: dict) -> list[str]:
    """A weight file this edit brings in that is turbo, distilled, few-step or quantized: a note, never a refusal."""
    from .graph_edit import model_files

    return [f"«{name}» — быстрые/квантизованные веса (turbo, distill, int8, fp8, gguf…): быстрее, качество может снизиться"
            for name in sorted(model_files(after) - model_files(before)) if FAST_WEIGHTS.search(name)]


def catalog(info: dict, query: str = "", limit: int = 50) -> list[dict]:
    """Node classes matching a search (name, display name, category, pack), with input names and outputs."""
    words = [word for word in query.lower().split() if word]
    found = []
    for name, node in info.items():
        haystack = " ".join(str(node.get(key) or "") for key in ("display_name", "category", "python_module", "description"))
        haystack = f"{name} {haystack}".lower()
        if all(word in haystack for word in words):
            inputs = node.get("input") or {}
            found.append({"class_type": name, "display_name": node.get("display_name") or name,
                          "category": node.get("category") or "", "pack": str(node.get("python_module") or "nodes"),
                          "required": list((inputs.get("required") or {})), "optional": list((inputs.get("optional") or {})),
                          "outputs": outputs(info, name), "output_node": bool(node.get("output_node"))})
    flat = "".join(words)

    def rank(item):
        name = item["class_type"].lower()
        shown = item["display_name"].lower().replace(" ", "")
        return (name != flat, flat not in name and flat not in shown,
                not all(word in f"{name} {item['display_name'].lower()}" for word in words), len(name), name)

    return sorted(found, key=rank)[:max(1, min(limit, 500))]


def describe(info: dict, class_type: str) -> dict:
    """One node class for a form: every input with its type, default, range or choices; outputs with names."""
    node = node_schema(info, class_type)
    fields = []
    for group in ("required", "optional"):
        for name, spec in ((node.get("input") or {}).get(group) or {}).items():
            limits = spec_limits(spec)
            options = combo_options(spec)
            fields.append({"name": name, "required": group == "required", "type": spec_type(spec),
                           "widget": spec_type(spec) in WIDGET_TYPES, "default": default_value(spec),
                           **({"choices": options} if options else {}),
                           **{key: limits[key] for key in ("min", "max", "step", "multiline", "tooltip") if key in limits}})
    return {"class_type": class_type, "display_name": node.get("display_name") or class_type,
            "category": node.get("category") or "", "pack": str(node.get("python_module") or "nodes"),
            "description": node.get("description") or "", "inputs": fields,
            "outputs": [{"type": kind, "name": (node.get("output_name") or [None] * len(outputs(info, class_type)))[index] or kind}
                        for index, kind in enumerate(outputs(info, class_type))]}
