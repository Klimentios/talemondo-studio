# STUDIO franchise workspace

Uses the unmodified MIT Drawnix source pinned in `vendor/drawnix/UPSTREAM.md`.
The STUDIO layer in `src/` supplies franchise/board navigation, domain cards,
local persistence, conflict handling and links to existing media. React is scoped
 to this independently built page; existing Studio modules are not converted.

## Build

From the repository root:

```sh
npm ci --prefix tools/franchise-board --ignore-scripts
npm run build --prefix tools/franchise-board
```

The build writes `preview/h3-director/franchise-board/`, including third-party
license notices. All assets are served locally. Commit the generated output with
source changes so the Mac service does not require a Node build on startup.

## API and storage

Regular Studio includes `local_studio/franchises.py`. Boards live in
`franchises.sqlite3` inside the existing Studio data directory; the original
production draft, asset files and jobs are not rewritten. Every save checks
an integer revision atomically and retains one previous content snapshot.
Export makes a separate JSON copy; imported asset IDs still refer to the same
Studio library and the JSON does not embed its media originals.

For a running Studio that predates these routes, a temporary local API is available:

```sh
.venv/bin/python -m uvicorn local_studio.franchise_api:app --host 127.0.0.1 --port 18208
```

The page checks `franchises_v1` in the main health response and uses this API only
when needed. It accepts browser requests only from local Studio origins.
After a normal Studio restart, the page uses the integrated API; the companion
can be stopped. It has no render dispatcher and does not change GPU jobs.

## Scope of this first version

Franchises, world/idea/episode boards, native Drawnix tools, cards with descriptions
and draft/approved status, library references, server autosave, export/import and
last-opened board. Characters, locations, rules, ideas and scenes have shared records inside each
franchise. Multiple board instances refer to the same record and keep independent
positions. The API refreshes record snapshots when a board is opened. Deleting
a board instance keeps the shared record. Generation handoff, outcome linking
and social analytics are later stages.

The UI audit fixed theme/zoom and narrow canvas toolbar overlaps. The board can
use native fullscreen or a viewport fallback; Escape and the collapse button exit.
Unapplied inspector text is saved before changing cards/boards. Async responses
are guarded against changing another selected card. Import blocks editing while
its replacement request is pending and makes independent copies of shared records.

Verified on an isolated Mac API: create an episode, add/edit cards, attach a local
library image, save, switch franchises, reload, export and reimport. Tests verify
persistence, cross-franchise isolation and rejection of stale writes. The live
page and existing library were checked read-only. Full check log:
`/tmp/studio-franchises-check.log` (258 JS, 805 Python, 4 skipped, 77 subtests).
The final API checks also passed: `tests/test_franchises.py tests/test_local_api.py`.
Production dependency audit at creation: no high or critical findings, four
moderate and one low; no blanket audit fix was applied to upstream dependencies.

## Product overview

Boards open in a visual overview, including new empty boards. It presents the
existing canvas content as a franchise hero, character imagery, reference
gallery and readable story sections. Long source text opens in a dedicated
reader. The `Холст` mode keeps the Drawnix canvas and editing tools available;
returning to `Обзор` saves the current edit first. Overview uses library asset
links already stored on the board and does not generate or copy media.

## Library picker and board management

Card materials support staged multi-selection, type/name search, visible loading
and validation, names/thumbnails and unlinking without deleting originals.
Canvas image insertion is separate from attachments. Modal async responses are
guarded by session and target; duplicate Apply is prevented.

Boards can be duplicated, archived/restored, searched and exported from the
previous save snapshot. Duplication intentionally shares franchise entity records.
Archived boards retain their complete content. The previous snapshot is one-save
recovery, not an unlimited history; export important milestones separately.

Picker follow-up verification (2026-10-12): real browser on isolated franchise
storage, reading the live library without modifying its files. Checked two-item
attachment, reopening with both preselected, clearing attachments, visible title
validation, duplicate with shared references, archive and restore. At 390x844 the
Apply button fits inside the dialog and attachment succeeds; in fullscreen Escape
closes the picker while preserving expanded board mode. Native selection loss on
inspector focus no longer dismisses the inspector before its button can click.
Targeted backend tests: 4 passed (`test_franchises.py`, `test_local_api.py`).

## Scene handoff

Scene cards expose “Подготовить в Studio”. Choose contextual cards on the board,
then review the brief and explicit reference roles/owners in the regular Studio.
The brief snapshot lives in browser localStorage on the same origin; it does not
embed media and is not a cloud transfer. Import adds a new scene with isolated
reference state and source franchise/board/entity IDs. Legacy scenes retain their
shared reference state. Existing generation buttons and settings remain in use.
No job is automatically submitted. Same packet ID never adds a second scene.

A failed final draft write retains the scene in memory and lets the review retry
saving that version. Stale tabs cannot import. Full verification:
`/tmp/studio-franchise-handoff-check.log` — 260 JS tests, 806 Python passed,
4 skipped, 77 subtests. Browser verification used isolated18207 storage and
read-only access to the live media library; no production render was started.
