#!/usr/bin/env python3
"""Summary over the probe-all corpus (logs/probes-all, scripts/h3_probe_collect.py).

  ~/H3-ComfyUI-mac/venv/bin/python scripts/h3_probe_corpus.py      # needs torch

Per job: sequence segments, attention of video rows on itself / reference latents / Qwen vision / prompt, and
attention per IR section relative to its token share (1.0x = as much as its length). Then the mean over jobs,
per block. Writes logs/probes-all/summary.md.
"""
from __future__ import annotations

import collections
import json
import re
import statistics
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "logs/probes-all"
SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music")


def sections(tokens: list[dict]) -> list[str]:
    texts = [t["text"].replace("Ġ", " ").replace("Ċ", "\n") for t in tokens]
    decoded = "".join(texts)
    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t)
    heads = [(m.start(), m.group(1)) for m in re.finditer(r"(?m)^(%s):" % "|".join(SECTIONS), decoded)]
    shot = decoded.find("[Shot 1")
    out = []
    for p in starts:
        name = "plain"
        for a, n in heads:
            if p >= a:
                name = n
        if name == "detailed_description" and 0 < shot and p < shot:
            name = "style_block"  # VISUAL DIRECTION / style opening before the first shot
        if name == "detailed_description":
            name = "shots"
        out.append(name)
    return out


def job_rows(folder: Path) -> list[dict]:
    tok = json.loads((folder / "tokens.json").read_text())
    start = tok["prompt_start"]
    secs = sections(tok["tokens"])
    share = collections.Counter(secs)
    rows = []
    for f in sorted(folder.glob("attn_s*_b*.pt")):
        d = torch.load(f)
        text = d["text_mass"].mean(0)
        seg = {k: float(v.mean()) for k, v in d["segment_mass"].items()}
        prompt = text[start:]
        by = collections.defaultdict(float)
        for i, s in enumerate(secs):
            by[s] += float(prompt[i])
        total = sum(by.values()) or 1.0
        rows.append({
            "step": d["step"], "block": d["block"],
            "self": sum(v for k, v in seg.items() if k.startswith("video")),
            "refs": sum(v for k, v in seg.items() if k.startswith(("ref", "cond"))),
            "vision": float(text[:start].sum()), "prompt": float(prompt.sum()),
            "ratio": {s: (by[s] / total) / (share[s] / len(secs)) for s in share},
        })
    return rows


def main() -> None:
    per_block: dict[int, list[dict]] = collections.defaultdict(list)
    lines = ["# Корпус probe-all", ""]
    jobs = sorted(p.parent for p in CORPUS.glob("*/job.json") if (p.parent / "tokens.json").exists())
    for folder in jobs:
        job = json.loads((folder / "job.json").read_text())
        rows = job_rows(folder)
        for r in rows:
            per_block[r["block"]].append(r)
        b = job.get("budget") or {}
        lines.append(f"- {folder.name[:8]} {job['state']} · {job['settings'].get('duration')} с · "
                     f"{len(job['references'])} реф. · строк {b.get('total_rows')} (×{b.get('attention_vs_target')})")
    lines += ["", f"Заданий: {len(jobs)}", "", "## Средние по блокам", ""]
    for block in sorted(per_block):
        rows = per_block[block]
        mean = lambda key: statistics.mean(r[key] for r in rows)
        ratios = collections.defaultdict(list)
        for r in rows:
            for s, v in r["ratio"].items():
                ratios[s].append(v)
        lines.append(f"- блок {block}: на себя {mean('self'):.2f}, латенты референсов {mean('refs'):.2f}, "
                     f"зрение Qwen {mean('vision'):.2f}, промпт {mean('prompt'):.3f}; секции (× к доле токенов): "
                     + ", ".join(f"{s} {statistics.mean(v):.1f}" for s, v in sorted(ratios.items())))
    (CORPUS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
