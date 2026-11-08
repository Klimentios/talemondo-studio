#!/usr/bin/env python3
"""H3 encoder research: rerun a finished Studio job with probes and read what the model listens to.

  python3 scripts/h3_probe.py run --job JOB_ID --name NAME [--steps 0,3,8,16,23] [--blocks 2,10,20,30,40,48]
  python3 scripts/h3_probe.py analyze NAME          # needs torch: run with ~/H3-ComfyUI-mac/venv/bin/python

run: takes the exact ComfyUI graph of the job from Studio, inserts H3ProbeCode after the reference conditioning
and H3ProbeAttention on the model the sampler uses, queues it on the GPU (same seed and settings: the video is
the same render), waits, and copies /workspace/h3/api-data/probes/NAME to logs/probes/NAME.
analyze: attention of video rows per segment, per prompt word and per craft group (camera, lens, light, ...)
for every recorded step and block -> logs/probes/NAME/report.md. Docs: docs/reference/H3_ENCODER_RESEARCH.md.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = "http://127.0.0.1:18187"
COMFY = "http://127.0.0.1:18188"
TUNNEL = Path.home() / "Library/Application Support/MiniMax H3/tunnel.sh"

# Craft groups for the report. A word counts for the first group whose pattern matches it.
GROUPS = {
    "camera": r"camera|shot|push|pull|dolly|pan|tilt|track|zoom|frame|frames|framing|close|closeup|medium|wide|angle|"
              r"handheld|crane|orbit|pov|overhead|low|high|reaches|begins|steady|slow",
    "lens": r"lens|\d*mm|focus|depth|field|bokeh|aperture|anamorphic|shallow|sharp|blur|blurred|grain|film",
    "light": r"light|lit|lights|lighting|lamp|lantern|glow|glowing|moonlight|sun|sunlight|shadow|shadows|tungsten|"
             r"neon|warm|cold|blue|orange|bright|dark|night|beam|backlit|rim|silhouette|flicker",
    "texture": r"wet|rain|raindrops|droplets|water|spray|wood|wooden|planks|rope|ropes|wool|oilskin|leather|metal|"
               r"glass|skin|texture|soaked|rough|weathered|stone|fabric|sweater|coat|beard|hair",
    "environment": r"deck|ship|sea|ocean|waves|wave|storm|sky|clouds|mast|rail|rigging|horizon|lighthouse|cafe|room|"
                   r"street|window|table|hull|boots|picture",
    "acting": r"eyes|face|brows|lips|jaw|chin|smile|smiles|laugh|laughs|tear|tears|fear|relief|tension|tired|"
              r"disbelieving|disbelief|trembles|trembling|freezes|widen|clenched|breath|breathing|exhales|expression|"
              r"exhausted|shaky|emotion|joy",
    "action": r"stands|grips|gripping|grip|holds|hands|leans|leaning|lowers|turns|looks|drags|wipes|picks|reads|"
              r"sagging|braced|bracing|bent|knees|scanning|searches|sweeps|loosens|moves|walks|runs",
    "sound": r"sound|soundscape|wind|roaring|crashing|drumming|creaking|rattling|murmur|music|audio|ambience|voice",
    "dialogue": r"says|said|speaks|whispers|hoarsely|softly|line|dialogue",
    "format": r"subject|summary|retention|detailed|description|definitions|overall|non|diegetic|preserved|partially|"
              r"reference|direction|contract|generate|cinematic|photoreal|vertical|live|action",
}


def http(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def ssh_target() -> tuple[str, str, str]:
    text = TUNNEL.read_text()
    get = lambda key: re.search(rf'^{key}="?([^"\n]+)"?', text, re.M).group(1)
    return get("SERVER_HOST"), get("SERVER_PORT"), get("SSH_KEY")


def insert_probes(graph: dict, name: str, steps: str, blocks: str, queries: int,
                  edits: str = "", attention: bool = True) -> dict:
    sys.path.insert(0, str(ROOT))
    from app.probe_graph import insert_probes as insert
    try:
        return _prefix_outputs(insert(graph, name, steps=steps, blocks=blocks, queries=queries,
                                      edits=edits, attention=attention), name)
    except ValueError as exc:
        raise SystemExit(str(exc))


def _prefix_outputs(graph: dict, name: str) -> dict:
    for node in graph.values():  # keep probe outputs apart from Studio results
        if "filename_prefix" in node["inputs"]:
            node["inputs"]["filename_prefix"] = f"probes/{name}"
    return graph


def cmd_run(args) -> None:
    if args.prompt_id:  # any graph ComfyUI ran (an A/B run on this server), not only a Studio job's
        job = {"graph": http("GET", f"{COMFY}/h3/studio/graph/{args.prompt_id}")["prompt"]}
    else:
        job = http("GET", f"{STUDIO}/api/jobs/{args.job}/graph")
    graph = insert_probes(job["graph"], args.name, args.steps, args.blocks, args.queries,
                          edits=args.edits, attention=not args.no_attention)
    out = ROOT / "logs/probes" / args.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1))
    queued = http("POST", f"{COMFY}/prompt", {"prompt": graph})
    pid = queued["prompt_id"]
    print(f"в очереди ComfyUI: {pid}")
    started = time.time()
    missing = 0
    while True:
        time.sleep(10)
        try:
            hist = http("GET", f"{COMFY}/history/{pid}")
            queue = http("GET", f"{COMFY}/queue")
        except OSError as exc:  # GPU or tunnel gone (preempted VM): give up, the caller retries
            missing += 1
            if missing >= 60:
                raise SystemExit(f"ComfyUI недоступен 10 мин: {exc}")
            continue
        listed = {item[1] for name in ("queue_running", "queue_pending") for item in queue.get(name, [])}
        if pid not in hist and pid not in listed:
            missing += 1
            if missing >= 6:  # ComfyUI restarted and forgot the prompt
                raise SystemExit("задание пропало из ComfyUI (перезапуск?)")
            continue
        missing = 0
        if pid in hist:
            status = hist[pid].get("status", {})
            print(f"готово за {int(time.time() - started)} с: {status.get('status_str')}")
            if status.get("status_str") != "success":
                print(json.dumps(status.get("messages", [])[-3:], ensure_ascii=False)[:2000])
                raise SystemExit(1)
            break
    # the rendered video(s) too: frame-by-frame comparison with the original job (probe must not change it)
    for node in hist[pid].get("outputs", {}).values():
        for item in [*node.get("videos", []), *node.get("gifs", []), *node.get("images", [])]:
            name = item.get("filename", "")
            if not name.endswith(".mp4"):
                continue
            query = urllib.parse.urlencode({"filename": name, "subfolder": item.get("subfolder", ""),
                                            "type": item.get("type", "output")})
            with urllib.request.urlopen(f"{COMFY}/view?{query}", timeout=600) as resp:
                (out / name).write_bytes(resp.read())
            print(f"видео: {out / name}")
    host, port, key = ssh_target()
    subprocess.run(["scp", "-q", "-r", "-i", key, "-P", port, "-o", "BatchMode=yes",
                    f"root@{host}:/workspace/h3/api-data/probes/{args.name}/.", str(out)], check=True)
    print(f"зонд: {out}")


def words(tokens: list[dict], start: int) -> list[tuple[str, list[int]]]:
    """Merge BPE pieces into words: [(word, [positions in the code])]."""
    merged: list[tuple[str, list[int]]] = []
    for i, tok in enumerate(tokens):
        text = tok["text"].replace("Ġ", " ").replace("Ċ", "\n")
        if not merged or text[:1] in (" ", "\n") or not re.match(r"[A-Za-z0-9']", text[:1]) \
                or not re.match(r"[A-Za-z0-9']", merged[-1][0][-1:] or " "):
            merged.append((text.strip(), [start + i]))
        else:
            merged[-1] = (merged[-1][0] + text, merged[-1][1] + [start + i])
    return [(w, p) for w, p in merged if w]


def group_of(word: str) -> str:
    w = word.lower().strip(".,;:!?\"'()[]<>")
    for group, pattern in GROUPS.items():
        if re.fullmatch(pattern, w):
            return group
    return "other"


def cmd_analyze(args) -> None:
    import torch

    out = ROOT / "logs/probes" / args.name
    meta = json.loads((out / "meta.json").read_text())
    tok = json.loads((out / "tokens.json").read_text())
    start = tok["prompt_start"]
    ws = words(tok["tokens"], start)
    lines = [f"# Зонд {args.name}", "", f"Код: {tok['seq_len']} позиций, из них промпт {len(tok['tokens'])} токенов "
             f"(с {start}); сегменты {meta['segments']}", ""]
    files = sorted(out.glob("attn_s*_b*.pt"))
    lines += ["## Доля внимания видео по сегментам (среднее по головам)", "",
              "| шаг | σ | блок | текст: префикс/референс | текст: промпт | референсы | звук | видео |", "|---|---|---|---|---|---|---|---|"]
    per_group: dict[tuple[int, int], dict[str, float]] = {}
    top_words: dict[tuple[int, int], list[tuple[str, float]]] = {}
    for f in files:
        d = torch.load(f)
        tm = d["text_mass"].mean(0)  # [text_len]
        seg = {k: float(v.mean()) for k, v in d["segment_mass"].items()}
        prompt = float(tm[start:].sum())
        prefix = float(tm[:start].sum())
        refs = sum(v for k, v in seg.items() if k.startswith(("ref", "cond")))
        audio = sum(v for k, v in seg.items() if k.startswith("audio"))
        video = sum(v for k, v in seg.items() if k.startswith("video"))
        lines.append(f"| {d['step']} | {d['sigma']:.3f} | {d['block']} | {prefix:.3f} | {prompt:.3f} | {refs:.3f} | {audio:.3f} | {video:.3f} |")
        wmass = [(w, float(tm[p].sum())) for w, p in ws]
        groups: dict[str, float] = {}
        for w, m in wmass:
            groups[group_of(w)] = groups.get(group_of(w), 0.0) + m
        total = sum(groups.values()) or 1.0
        per_group[(d["step"], d["block"])] = {g: v / total for g, v in groups.items()}
        top_words[(d["step"], d["block"])] = sorted(wmass, key=lambda x: -x[1])[:15]
    names = list(GROUPS) + ["other"]
    lines += ["", "## Внимание к промпту по группам (доля от внимания к промпту)", "",
              "| шаг | блок | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for (s, b), g in sorted(per_group.items()):
        lines.append(f"| {s} | {b} | " + " | ".join(f"{g.get(n, 0):.2f}" for n in names) + " |")
    lines += ["", "## Слова, которые видео слушает сильнее всего", ""]
    for (s, b), top in sorted(top_words.items()):
        lines.append(f"- шаг {s}, блок {b}: " + ", ".join(f"{w} {m:.4f}" for w, m in top))
    counts: dict[str, int] = {}
    for w, _ in ws:
        counts[group_of(w)] = counts.get(group_of(w), 0) + 1
    lines += ["", "Слов в промпте по группам: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out / "report.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--job", default="")
    run.add_argument("--prompt-id", default="", help="ID задания ComfyUI вместо задания Studio")
    run.add_argument("--name", required=True)
    run.add_argument("--steps", default="0,3,8,16,23")
    run.add_argument("--blocks", default="2,10,20,30,40,48")
    run.add_argument("--queries", type=int, default=512)
    run.add_argument("--edits", default="", help='JSON [{"phrase": "...", "scale": 0.0}] — абляция фраз в коде')
    run.add_argument("--no-attention", action="store_true", help="только видео (и код), без карты внимания")
    ana = sub.add_parser("analyze")
    ana.add_argument("name")
    args = parser.parse_args()
    {"run": cmd_run, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
