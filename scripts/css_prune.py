"""Prune the Studio main-page CSS: whole rules whose every selector names a class/id that exists nowhere
in HTML/JS, and declarations overridden by a later identical selector (same @media, same property, not
weaker importance) on EVERY page that loads the file. Usage: python3 scripts/css_prune.py [preview/h3-director] [--apply]  (dry run prints counts per file)"""
import re, sys, json
from pathlib import Path
from collections import defaultdict

ROOT = Path(next((a for a in sys.argv[1:] if not a.startswith("--")), "preview/h3-director")).resolve(); APPLY = '--apply' in sys.argv
corpus_files = [p for p in ROOT.rglob('*') if p.suffix in {'.html', '.mjs', '.js', '.json'}]
corpus_files += list((ROOT.parents[1] / 'local_studio').rglob('*.py'))
corpus = '\n'.join(p.read_text(errors='ignore') for p in corpus_files)
words = set(re.findall(r'[A-Za-z_][\w-]*', corpus))
prefixes = set(re.findall(r'([A-Za-z_][\w-]*[-_])\$\{', corpus)) | set(re.findall(r'''['"`]([A-Za-z_][\w-]*[-_])['"`]\s*\+''', corpus))
used = lambda t: t in words or any(t.startswith(p) for p in prefixes)

# pages → ordered css list (resolved paths)
pages = {}
for html in ROOT.rglob('*.html'):
    hrefs = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"?]+\.css)', html.read_text()) + \
            re.findall(r'<link[^>]*href="([^"?]+\.css)[^"]*"[^>]*rel="stylesheet"', html.read_text())
    if hrefs:
        pages[html] = [(html.parent / h).resolve() for h in hrefs if not h.startswith('http')]
index = (ROOT / 'index.html')
targets = [p for p in pages[index.resolve() if index.resolve() in pages else index]
           if p.name not in {'tokens.css', 'components.css'}]

def blank_comments(s):
    return re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group()), s, flags=re.S)

def split_top(sel):
    out, depth, cur = [], 0, ''
    for ch in sel:
        depth += ch in '([' ; depth -= ch in ')]'
        if ch == ',' and depth == 0: out.append(cur); cur = ''
        else: cur += ch
    out.append(cur)
    return [' '.join(x.split()) for x in out if x.strip()]

def sel_dead(sel):
    # :where()/:is() lists match when ANY alternative exists, so their tokens never kill a selector.
    body = re.sub(r':(?:not|where|is|matches)\((?:[^()]|\([^()]*\))*\)', '', sel)
    body = re.sub(r'\[[^\]]*\]', '', body)
    return any(not used(t) for t in re.findall(r'[.#](-?[A-Za-z_][\w-]*)', body))

def parse(path):
    raw = path.read_text(); text = blank_comments(raw); rules = []
    def walk(i, ctx):
        n = len(text)
        while i < n:
            j = text.find('{', i); k = text.find('}', i)
            if j == -1 or (k != -1 and k < j): return (k + 1) if k != -1 else n
            prelude = ' '.join(text[i:j].split())
            if prelude.startswith(('@media', '@supports', '@container', '@layer')):
                i = walk(j + 1, ctx + (prelude,)); continue
            d, m = 1, j + 1
            while d and m < n:
                d += text[m] == '{'; d -= text[m] == '}'; m += 1
            if not prelude.startswith('@') and not re.match(r'^(from|to|[\d.]+%)', prelude):
                decls = []
                body_start = j + 1; pos = body_start; body = text[body_start:m - 1]
                depth = 0; start = 0
                for q, ch in enumerate(body + ';'):
                    depth += ch == '('; depth -= ch == ')'
                    if ch == ';' and depth == 0:
                        seg = body[start:q]
                        if ':' in seg and seg.strip():
                            prop = seg.split(':', 1)[0].strip().lower()
                            lead = len(seg) - len(seg.lstrip())
                            decls.append({'prop': prop, 'imp': '!important' in seg,
                                          'start': body_start + start + lead,
                                          'end': body_start + min(q + 1, len(body))})
                        start = q + 1
                rules.append({'ctx': ctx, 'sels': split_top(prelude), 'start': i, 'open': j, 'end': m, 'decls': decls})
            i = m
        return n
    walk(0, ())
    return raw, rules

parsed = {p: parse(p) for p in {p for lst in pages.values() for p in lst} if p.exists()}

# shadow check per page
shadowed = defaultdict(lambda: None)  # (file, rule_idx, decl_idx) -> True/False aggregated over pages
for page, css_list in pages.items():
    order = []
    for f in css_list:
        if f not in parsed: continue
        for ri, r in enumerate(parsed[f][1]):
            for di, d in enumerate(r['decls']):
                order.append((f, ri, di, r, d))
    # for each (ctx, sel, prop) find later declarations
    last = {}
    flags = {}
    for idx in range(len(order) - 1, -1, -1):
        f, ri, di, r, d = order[idx]
        ok = True
        for s in r['sels']:
            later = last.get((r['ctx'], s, d['prop']))
            if later is None or (d['imp'] and not later): ok = False
        flags[(f, ri, di)] = ok
        for s in r['sels']:
            key = (r['ctx'], s, d['prop'])
            last[key] = last.get(key, False) or d['imp'] if key in last else d['imp']
    for key, ok in flags.items():
        shadowed[key] = ok if shadowed[key] is None else (shadowed[key] and ok)

summary = {}
for f in targets:
    raw, rules = parsed[f]
    cuts = []
    dead_rules = shadow = 0
    for ri, r in enumerate(rules):
        if r['sels'] and all(sel_dead(s) for s in r['sels']):
            cuts.append((r['start'], r['end'])); dead_rules += 1; continue
        gone = [d for di, d in enumerate(r['decls']) if shadowed[(f, ri, di)]]
        if gone and len(gone) == len(r['decls']):
            cuts.append((r['start'], r['end'])); shadow += len(gone); continue
        for d in gone:
            cuts.append((d['start'], d['end'])); shadow += 1
    summary[f.name] = (dead_rules, shadow)
    if APPLY and cuts:
        out = raw
        for a, b in sorted(cuts, reverse=True):
            out = out[:a] + out[b:]
        # tidy: empty @media blocks, empty rules, blank-line runs, whitespace-only lines
        for _ in range(3):
            out = re.sub(r'@(media|supports|container)[^{]*\{\s*\}', '', out)
            out = re.sub(r'[^{}\n;/]+\{\s*\}', '', out)
        out = re.sub(r'[ \t]+\n', '\n', out)
        out = re.sub(r'\n{3,}', '\n\n', out).lstrip('\n')
        f.write_text(out)
print(json.dumps(summary, ensure_ascii=False))
print('pages:', {p.name: len(v) for p, v in pages.items()})
