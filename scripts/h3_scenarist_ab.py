#!/usr/bin/env python3
"""A/B of the Scenarist model: the same Russian briefs through /api/scenarist of two GPU APIs.

  # two servers (or two tunnels), one per profile:
  python3 scripts/h3_scenarist_ab.py --a 8b=http://127.0.0.1:18189 --b 27b=http://127.0.0.1:28189
  # one server, profiles one after another (switch with scripts/h3_scenarist_switch.sh between):
  python3 scripts/h3_scenarist_ab.py --only 8b=http://127.0.0.1:18189
  python3 scripts/h3_scenarist_ab.py --only 27b=http://127.0.0.1:18189
  python3 scripts/h3_scenarist_ab.py --compare 8b 27b

Every answer is saved, re-checked with app.h3_ir.validate_ir (same skeleton and shot headers the
server built), and scanned for refusals. Table: valid format %, problems per prompt, refusals,
errors, seconds. Outputs: logs/scenarist-ab/<label>.json (gitignored). Never renders.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.h3_ir import Reference, build_skeleton, shot_starts, validate_ir  # noqa: E402

DEFAULT_BRIEFS = ROOT / "scripts" / "data" / "scenarist_ab_briefs.json"
DEFAULT_OUT = ROOT / "logs" / "scenarist-ab"

# Typical refusal openings of Qwen-family models (English and Russian). A refusal in place of the
# six IR sections is what a stronger model must not do for clearly fictional heist/weapon scenes.
REFUSAL = re.compile(
    r"(?i)\b(i can(?:'|’)?t|i cannot|i(?:'|’)m (?:sorry|unable|not able)|i am (?:sorry|unable|not able)|"
    r"i won(?:'|’)t|i must decline|as an ai|cannot (?:help|assist|comply|create|provide)|"
    r"unable to (?:help|assist|comply|fulfil|fulfill)|against (?:my|the) (?:guidelines|policy|policies)|"
    r"not (?:able|allowed) to (?:help|assist|create|provide))"
    r"|(?:извините|к сожалению)[, ]+(?:но )?я не (?:могу|буду)|я не могу (?:помочь|выполнить|создать|написать)"
    r"|не могу (?:помочь|выполнить) (?:с )?(?:этим|этот|эту)|это противоречит (?:моим|правилам)")


def is_refusal(text: str) -> bool:
    head = (text or "")[:600]
    return bool(REFUSAL.search(head)) and "summary:" not in (text or "").lower()


def load_briefs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    briefs = data["briefs"] if isinstance(data, dict) else data
    ids = [b["id"] for b in briefs]
    if len(set(ids)) != len(ids):
        raise SystemExit("Brief ids must be unique")
    return briefs


def request_body(brief: dict) -> dict:
    return {"brief": brief["brief"], "references": brief.get("references", []),
            "cut_durations": brief.get("cut_durations", [10.0]),
            "speaking_owners": brief.get("speaking_owners", []), "style_hint": brief.get("style_hint", ""),
            "use_video_audio": bool(brief.get("use_video_audio", False))}


def post_scenarist(base_url: str, body: dict, timeout: float = 600) -> tuple[int, dict | str]:
    request = urllib.request.Request(base_url.rstrip("/") + "/api/scenarist", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read() or b"").decode(errors="replace")


def local_problems(brief: dict, prompt: str) -> list[str]:
    """The server's validate_ir again, locally: same skeleton and shot headers from the brief."""
    refs = [Reference(r["kind"], r["number"], r["role"], r.get("owner", "")) for r in brief.get("references", [])]
    skeleton = build_skeleton(refs, speaking_owners=brief.get("speaking_owners", []),
                              use_video_audio=bool(brief.get("use_video_audio", False)))
    return validate_ir(prompt, skeleton, shot_starts(brief.get("cut_durations", [10.0])))


def run_one(base_url: str, brief: dict, *, busy_retries: int = 20, busy_wait: float = 30,
            post=post_scenarist, sleep=time.sleep) -> dict:
    body = request_body(brief)
    for _ in range(busy_retries + 1):
        started = time.monotonic()
        status, payload = post(base_url, body)
        seconds = time.monotonic() - started
        if status != 409:  # 409 = a render is running; the scenarist waits for an idle GPU
            break
        sleep(busy_wait)
    row = {"id": brief["id"], "status": status, "seconds": round(seconds, 2)}
    if status != 200 or not isinstance(payload, dict):
        return {**row, "error": str(payload)[:500], "prompt": "", "problems": [], "refusal": False, "valid": False}
    prompt = str(payload.get("prompt") or "")
    problems = local_problems(brief, prompt)
    return {**row, "prompt": prompt, "server_problems": payload.get("problems", []), "problems": problems,
            "refusal": is_refusal(prompt), "valid": not problems}


def run_label(label: str, base_url: str, briefs: list[dict], out: Path, **kwargs) -> dict:
    rows = []
    for index, brief in enumerate(briefs, 1):
        row = run_one(base_url, brief, **kwargs)
        rows.append(row)
        flag = "OK " if row["valid"] else ("REF" if row["refusal"] else ("ERR" if row.get("error") else "BAD"))
        print(f"[{label} {index}/{len(briefs)}] {flag} {brief['id']} {row['seconds']:.1f}s "
              f"problems={len(row['problems'])}", flush=True)
    result = {"label": label, "url": base_url, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def summarize(result: dict) -> dict:
    rows = result["rows"]
    answered = [r for r in rows if not r.get("error")]
    seconds = [r["seconds"] for r in answered] or [0.0]
    return {"label": result["label"], "n": len(rows),
            "valid_pct": round(100 * sum(r["valid"] for r in rows) / max(1, len(rows)), 1),
            "problems_per_prompt": round(sum(len(r["problems"]) for r in answered) / max(1, len(answered)), 2),
            "refusals": sum(r["refusal"] for r in rows), "errors": len(rows) - len(answered),
            "seconds_avg": round(statistics.mean(seconds), 1), "seconds_p50": round(statistics.median(seconds), 1)}


def table(results: list[dict]) -> str:
    head = ["profile", "briefs", "valid %", "problems/prompt", "refusals", "errors", "avg s", "p50 s"]
    keys = ["label", "n", "valid_pct", "problems_per_prompt", "refusals", "errors", "seconds_avg", "seconds_p50"]
    lines = [head] + [[str(summarize(r)[k]) for k in keys] for r in results]
    widths = [max(len(line[i]) for line in lines) for i in range(len(head))]
    text = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)) for line in lines]
    text.insert(1, "  ".join("-" * w for w in widths))
    if len(results) == 2:  # per-brief verdicts where the two disagree
        a, b = ({r["id"]: r for r in res["rows"]} for res in results)
        diff = [f"  {i}: {results[0]['label']}={'ok' if a[i]['valid'] else 'ref' if a[i]['refusal'] else 'bad'}"
                f" {results[1]['label']}={'ok' if b[i]['valid'] else 'ref' if b[i]['refusal'] else 'bad'}"
                for i in a if i in b and (a[i]["valid"], a[i]["refusal"]) != (b[i]["valid"], b[i]["refusal"])]
        if diff:
            text += ["", "Different verdicts:"] + diff
    return "\n".join(text)


def labelled(value: str) -> tuple[str, str]:
    label, sep, url = value.partition("=")
    if not sep or not re.fullmatch(r"[A-Za-z0-9._-]+", label) or not url.startswith("http"):
        raise argparse.ArgumentTypeError("use LABEL=http://host:port")
    return label, url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--briefs", type=Path, default=DEFAULT_BRIEFS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--a", type=labelled)
    parser.add_argument("--b", type=labelled)
    parser.add_argument("--only", type=labelled, help="run one profile now, compare later")
    parser.add_argument("--compare", nargs=2, metavar="LABEL", help="table from saved runs")
    parser.add_argument("--limit", type=int, default=0, help="first N briefs only")
    args = parser.parse_args(argv)
    if args.compare:
        results = [json.loads((args.out / f"{label}.json").read_text(encoding="utf-8")) for label in args.compare]
    else:
        targets = [t for t in (args.a, args.b, args.only) if t]
        if not targets or (args.only and (args.a or args.b)):
            parser.error("--a and --b, or --only, or --compare")
        briefs = load_briefs(args.briefs)[: args.limit or None]
        results = [run_label(label, url, briefs, args.out) for label, url in targets]
    print()
    print(table(results))
    print(f"\nSaved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
