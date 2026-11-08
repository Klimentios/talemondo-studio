---
name: visual-dramaturgy
license: CC-BY-4.0 (attribution required — Serge Shima, github.com/smixs/visual-skills; adapted for STUDIO, model-specific files removed)
description: Shot-level dramaturgy for AI video — scene formula, the three-detail law, three jobs per shot, Murch's Rule of Six, five anchors, blocking as choreography of desire, three-layer storyboard, 14-field shot card, rhythm ladder, montage patterns and genre arcs. Use before writing or auditing any STUDIO H3 shot list or prompt, to check that every shot has pressure, a physical micro-action and a motif instead of adjectives.
---

# Visual dramaturgy (craft core from smixs/visual-skills)

Hybrid role: direct, write, edit — prompt engineering comes last. The craft lives in the reference files; read them before writing.

## Reading order

1. [references/dramaturgy.md](references/dramaturgy.md) — scene formula, Details Law, Murch Rule of Six, three-jobs rule, five anchors, blocking, three-layer storyboard, 14-field shot card, rhythm ladder, dramaturgy check. Always.
2. [references/universal-rules.md](references/universal-rules.md) — U1–U12 rules for every video model (weight at start, show-don't-tell, lens language, character anchor, final image).
3. As needed: [patterns-and-genres.md](references/patterns-and-genres.md) (montage patterns, timecoded beats inside one clip, story arcs incl. kishotenketsu), [role-modes.md](references/role-modes.md), [fixes-and-skeletons.md](references/fixes-and-skeletons.md), [race-and-speed.md](references/race-and-speed.md) (kinetic montage, Ritchie paragraph), [camera-lighting-vocabulary.md](references/camera-lighting-vocabulary.md).

## STUDIO adaptation

- The original model files (Seedance, Kling, Veo) are intentionally not installed. Where a reference points to them, compile the final text with `h3-prompt-writing` (MiniMax H3 schema, `[Shot N] At MM:SS.mmm`, `<d>[Language] …</d>`).
- Studio rules outrank this skill: the user's decisions in `docs/DECISIONS.md` (language, aspect ratio), ≤15 s per H3 request, `docs/guides/PRODUCER_QC.md`.
