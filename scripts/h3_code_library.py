#!/usr/bin/env python3
"""Library of the Qwen code H3 received, built from probe folders (docs/reference/H3_ENCODER_RESEARCH.md).

  python3 scripts/h3_code_library.py            # index → logs/code-library/index.json + summary.md
  python3 scripts/h3_code_library.py --phrase "his eyes widen"   # where the phrase occurs, with code norms

Every probe folder (logs/probes-all/<id>, logs/probes/<name>) with code.pt + tokens.json is one entry: the prompt
tokens H3 saw, where the prompt starts in the code sequence, the code norm per word, and whether the full H3 input
(h3_input.pt: code with extras + latent) is there — the file H3CodeLoad renders from without Qwen. The index is the
map of «Qwen's language» for later work: reuse, splice and edit codes instead of re-encoding.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "logs/probes-all", ROOT / "logs/probes"]
OUT = ROOT / "logs/code-library"


def words(tokens: list) -> list[tuple[str, list[int]]]:
    """Qwen tokens ({"id", "text"} with decoded text, a leading space starts a word) → words with token indices."""
    out: list[tuple[str, list[int]]] = []
    for i, tok in enumerate(tokens):
        text = (tok["text"] if isinstance(tok, dict) else str(tok)).replace("Ġ", " ").replace("Ċ", "\n")
        if not out or text[:1].isspace() or not text.strip():
            out.append((text.strip(), [i]))
        else:
            out[-1] = (out[-1][0] + text, out[-1][1] + [i])
    return [(w, idx) for w, idx in out if w]


def entry(folder: Path) -> dict | None:
    code_file, tokens_file = folder / "code.pt", folder / "tokens.json"
    if not code_file.exists() or not tokens_file.exists():
        return None
    import torch
    tok = json.loads(tokens_file.read_text(encoding="utf-8"))
    code = torch.load(code_file, map_location="cpu")["code"][0].float()  # [seq, 5120]
    start = int(tok["prompt_start"])
    ws = words(tok["tokens"])
    norms = [round(float(code[[start + i for i in idx]].norm(dim=-1).mean()), 2) for _, idx in ws]
    job = folder / "job.json"
    return {"id": folder.name, "folder": str(folder.relative_to(ROOT)), "seq_len": int(tok["seq_len"]),
            "prompt_start": start, "prompt_tokens": len(tok["tokens"]), "words": [w for w, _ in ws], "norms": norms,
            "h3_input": (folder / "h3_input.pt").exists(),
            "research": json.loads(job.read_text()).get("research", False) if job.exists() else True}


def build() -> list[dict]:
    entries = []
    for source in SOURCES:
        for folder in sorted(p for p in source.glob("*") if p.is_dir()):
            try:
                e = entry(folder)
            except Exception as exc:  # one broken folder must not stop the index
                print("пропуск", folder.name, repr(exc)[:120])
                continue
            if e:
                entries.append(e)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    vocab = {w.lower() for e in entries for w in e["words"]}
    lines = ["# Библиотека кода Qwen → H3", "",
             f"Записей: {len(entries)}; с полным входом H3 (рендер без Qwen): {sum(e['h3_input'] for e in entries)}; "
             f"рабочих: {sum(not e['research'] for e in entries)}; слов в словаре: {len(vocab)}.", "",
             "| запись | токенов кода | токенов промпта | вход H3 |", "|---|---|---|---|"]
    lines += [f"| {e['id'][:24]} | {e['seq_len']} | {e['prompt_tokens']} | {'да' if e['h3_input'] else '—'} |"
              for e in entries]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries


def find(entries: list[dict], phrase: str) -> None:
    target = phrase.lower().split()
    for e in entries:
        low = [w.lower().strip(".,:;!?\"'") for w in e["words"]]
        for i in range(len(low) - len(target) + 1):
            if low[i:i + len(target)] == target:
                print(f"{e['id'][:24]} слово {i}: нормы {e['norms'][i:i + len(target)]}"
                      f"{' · вход H3 есть' if e['h3_input'] else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phrase")
    args = parser.parse_args()
    entries = build()
    print(OUT / "summary.md", f"записей {len(entries)}")
    if args.phrase:
        find(entries, args.phrase)


if __name__ == "__main__":
    main()
