"""H3 encoder research probes (docs/reference/H3_ENCODER_RESEARCH.md); installed into custom_nodes/h3-probe.

Read-only instruments, they never change the picture:
- H3ProbeCode: saves the Qwen layer-50 conditioning ("the code" H3 reads) and the prompt's token strings.
- H3ProbeAttention: patches the model's attention (transformer_options optimized_attention_override) and, at the
  chosen sampler steps and DiT blocks, measures how much attention a sample of video rows gives to every key
  row: each prompt token, the picture/vision rows, the reference blocks, audio and video. Softmax is computed
  exactly over the full sequence for the sampled queries, per head, in float32; the render itself still runs the
  normal attention kernel on the original tensors.
Files: /workspace/h3/api-data/probes/<name>/ (code.pt, tokens.json, attn_s<step>_b<block>.pt, meta.json).
"""
import json
import os
from pathlib import Path

import torch

PROBE_ROOT = Path(os.environ.get("H3_PROBE_DIR", "/workspace/h3/api-data/probes"))


def _probe_dir(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "probe"
    path = PROBE_ROOT / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ints(text: str) -> list[int]:
    return sorted({int(part) for part in str(text).replace(" ", "").split(",") if part != ""})


def prompt_tokens(clip, prompt: str) -> list[dict]:
    """Token ids and strings of the prompt text exactly as the H3 tokenizer adds it (after the references)."""
    tok = clip.tokenizer.qwen3vl_32b
    pairs = tok.tokenize_with_weights(prompt, return_word_ids=False, disable_weights=True)[0]
    out = []
    for item in clip.tokenizer.untokenize(pairs):
        (token_id, _weight), text = item[0], item[1]
        out.append({"id": int(token_id), "text": text})
    return out


class H3ProbeCode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"conditioning": ("CONDITIONING",), "clip": ("CLIP",),
                             "prompt": ("STRING", {"multiline": True}), "name": ("STRING", {"default": "probe"})}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "probe"
    CATEGORY = "STUDIO/H3 research"
    DESCRIPTION = "Save the Qwen layer-50 code H3 receives plus the prompt token strings; passes the conditioning on unchanged."

    def probe(self, conditioning, clip, prompt, name):
        try:
            self._save(conditioning, clip, prompt, name)
        except Exception as exc:  # research only: the render must go on
            try:
                (_probe_dir(name) / "code_error.txt").write_text(repr(exc), encoding="utf-8")
            except OSError:
                pass
        return (conditioning,)

    def _save(self, conditioning, clip, prompt, name):
        out = _probe_dir(name)
        code, extra = conditioning[0][0], conditioning[0][1]
        tokens = prompt_tokens(clip, prompt)
        tags = extra.get("minimax_token_tags")
        torch.save({"code": code.detach().to("cpu", torch.float16),
                    "token_tags": None if tags is None else torch.as_tensor(tags).cpu()}, out / "code.pt")
        seq = int(code.shape[1])
        (out / "tokens.json").write_text(json.dumps(
            {"seq_len": seq, "prompt_start": seq - len(tokens), "tokens": tokens}, ensure_ascii=False), encoding="utf-8")


class _AttentionRecorder:
    def __init__(self, out: Path, steps: list[int], blocks: list[int], queries: int, seed: int):
        self.out, self.steps, self.blocks, self.queries, self.seed = out, set(steps), set(blocks), queries, seed
        self.sigmas: list[float] = []
        self.meta_written = False

    def _step(self, transformer_options) -> int | None:
        sigmas = transformer_options.get("sigmas")
        if sigmas is None:
            return None
        sigma = round(float(sigmas.flatten()[0]), 6)
        if sigma not in self.sigmas:
            self.sigmas.append(sigma)
        return self.sigmas.index(sigma)

    def __call__(self, func, q, k, v, heads, *args, **kwargs):
        opts = kwargs.get("transformer_options") or {}
        layout = opts.get("minimax_h3_layout")
        block = opts.get("block_index")
        step = self._step(opts)
        if (layout is not None and step in self.steps and block in self.blocks
                and q.dim() == 4 and q.shape[2] == layout.seq_len):
            try:
                self._record(q, k, layout, step, block)
            except Exception as exc:  # a research probe must never break the render
                (self.out / f"error_s{step}_b{block}.txt").write_text(repr(exc), encoding="utf-8")
        return func(q, k, v, heads, *args, **kwargs)

    @torch.no_grad()
    def _record(self, q, k, layout, step, block):
        segments = [(int(a), int(b), kind) for a, b, kind in layout.segments]
        video = [(a, b) for a, b, kind in segments if kind == "video"]
        if not video:
            return
        a, b = video[0]
        gen = torch.Generator(device="cpu").manual_seed(self.seed + block)
        n = min(self.queries, b - a)
        rows = (torch.randperm(b - a, generator=gen)[:n] + a).to(q.device)
        heads, seq, dim = k.shape[1], k.shape[2], k.shape[3]
        scale = dim ** -0.5
        mass = torch.zeros(heads, seq, device=q.device)  # [H, S]
        # 4 heads at a time in float32 (~70 MB keys + ~270 MB probabilities): the GPU is nearly full during H3
        for h in range(0, heads, 4):
            qs = q[0, h:h + 4, rows, :].float()            # [4, n, d]
            keys = k[0, h:h + 4].float()                   # [4, S, d]
            probs = torch.softmax(qs @ keys.transpose(1, 2) * scale, dim=-1)  # [4, n, S]
            mass[h:h + 4] = probs.mean(dim=1)
            del qs, keys, probs
        text_end = next(stop for start, stop, kind in segments if kind == "text")
        torch.save({"step": step, "sigma": self.sigmas[step], "block": block, "queries": n,
                    "text_mass": mass[:, :text_end].cpu(),                      # [H, text_len]
                    "segment_mass": {f"{kind}@{start}": mass[:, start:stop].sum(1).cpu()
                                     for start, stop, kind in segments}},     # each [H]
                   self.out / f"attn_s{step:02d}_b{block:02d}.pt")
        if not self.meta_written:
            (self.out / "meta.json").write_text(json.dumps(
                {"segments": segments, "heads": int(heads), "seq_len": int(seq)}), encoding="utf-8")
            self.meta_written = True


class H3ProbeAttention:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",), "name": ("STRING", {"default": "probe"}),
                             "steps": ("STRING", {"default": "0,3,8,16,23"}),
                             "blocks": ("STRING", {"default": "2,10,20,30,40,48"}),
                             "queries": ("INT", {"default": 512, "min": 32, "max": 4096}),
                             "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1})}}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "STUDIO/H3 research"
    DESCRIPTION = "Record where sampled video rows attend (per prompt token, per segment) at chosen steps and blocks."

    def patch(self, model, name, steps, blocks, queries, seed):
        m = model.clone()
        opts = dict(m.model_options.get("transformer_options", {}))  # own copy: never patch the shared model
        m.model_options["transformer_options"] = opts
        if "optimized_attention_override" in opts:
            raise ValueError("H3ProbeAttention: another attention override is already active on this model")
        opts["optimized_attention_override"] = _AttentionRecorder(_probe_dir(name), _ints(steps), _ints(blocks),
                                                                  queries, seed)
        return (m,)



class H3ProbeEditCode:
    """Ablation: scale the code rows of chosen prompt phrases (research only; changes the render on purpose)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"conditioning": ("CONDITIONING",), "clip": ("CLIP",),
                             "prompt": ("STRING", {"multiline": True}),
                             "edits": ("STRING", {"multiline": True, "default": "[]"}),
                             "name": ("STRING", {"default": "probe"})}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "edit"
    CATEGORY = "STUDIO/H3 research"
    DESCRIPTION = ('edits = JSON [{"phrase": "his eyes widen", "scale": 0.0}, ...]: every occurrence of the phrase in '
                   "the prompt gets its Qwen layer-50 rows multiplied by scale (0 = the model does not hear it).")

    def edit(self, conditioning, clip, prompt, edits, name):
        plan = json.loads(edits or "[]")
        tokens = prompt_tokens(clip, prompt)
        texts = [t["text"] for t in tokens]
        # character offset of every token inside the decoded prompt
        decoded, starts = "", []
        for text in texts:
            starts.append(len(decoded))
            decoded += text.replace("Ġ", " ").replace("Ċ", "\n")
        out, log = [], []
        for code, extra in conditioning:
            code = code.clone()
            first = code.shape[1] - len(tokens)
            for item in plan:
                phrase, scale = str(item["phrase"]), float(item["scale"])
                hits = 0
                at = decoded.find(phrase)
                while at >= 0:
                    rows = [i for i, s0 in enumerate(starts) if at <= s0 < at + len(phrase)
                            or (s0 <= at < s0 + len(texts[i]))]
                    for i in rows:
                        code[:, first + i] *= scale
                    hits += 1
                    at = decoded.find(phrase, at + len(phrase))
                log.append({"phrase": phrase, "scale": scale, "occurrences": hits})
            out.append([code, extra])
        (_probe_dir(name) / "edits.json").write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        missing = [e["phrase"] for e in log if not e["occurrences"]]
        if missing:
            raise ValueError("H3ProbeEditCode: phrases not found in the prompt: " + "; ".join(missing))
        return (out,)

class H3CodeSave:
    """Research: store everything the sampler gets from the Ref2VA node (conditioning with all extras, latent) in
    the original dtypes, so a later graph can run H3 from it without Qwen (H3CodeLoad). Passes both on unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"conditioning": ("CONDITIONING",), "latent": ("LATENT",),
                             "name": ("STRING", {"default": "code-cache"})}}

    RETURN_TYPES = ("CONDITIONING", "LATENT")
    FUNCTION = "save"
    CATEGORY = "STUDIO/H3 research"

    def save(self, conditioning, latent, name):
        torch.save({"conditioning": _to_cpu(conditioning), "latent": _to_cpu(latent)}, _probe_dir(name) / "h3_input.pt")
        return (conditioning, latent)


class H3CodeLoad:
    """Research: the Ref2VA outputs from a file written by H3CodeSave. A graph wired to this node never runs the
    Ref2VA node or the Qwen text encoder (ComfyUI executes only what the outputs need)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"name": ("STRING", {"default": "code-cache"})}}

    RETURN_TYPES = ("CONDITIONING", "LATENT")
    FUNCTION = "load"
    CATEGORY = "STUDIO/H3 research"

    @classmethod
    def IS_CHANGED(cls, name):
        path = _probe_dir(name) / "h3_input.pt"
        return path.stat().st_mtime if path.exists() else float("nan")

    def load(self, name):
        data = torch.load(_probe_dir(name) / "h3_input.pt", map_location="cpu", weights_only=False)
        return (data["conditioning"], data["latent"])


def _to_cpu(value):
    if isinstance(value, torch.Tensor):
        return value.detach().to("cpu")
    if isinstance(value, dict):
        return {k: _to_cpu(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_to_cpu(v) for v in value)
    return value


NODE_CLASS_MAPPINGS = {"H3ProbeCode": H3ProbeCode, "H3ProbeAttention": H3ProbeAttention,
                       "H3ProbeEditCode": H3ProbeEditCode, "H3CodeSave": H3CodeSave, "H3CodeLoad": H3CodeLoad}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ProbeCode": "H3 зонд: код энкодера", "H3ProbeAttention": "H3 зонд: карта внимания",
                              "H3ProbeEditCode": "H3 зонд: правка кода (абляция)",
                              "H3CodeSave": "H3: сохранить вход (код+латент)", "H3CodeLoad": "H3: вход из файла (без Qwen)"}
