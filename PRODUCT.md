# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Assumption to validate: a creator or director who assembles references and generates short videos in H3 Studio with MiniMax H3. The user is working from the local Mac Studio client and may be waiting for a remote GPU to become available.

## Product Purpose

H3 Studio lets a creator prepare image, video, and audio references, describe a shot, submit it to a local queue for MiniMax H3, and review the resulting video. The requested product structure separates creation, story organization, editing, and the asset library into distinct workspaces.

## Operating Context

The browser interface runs on the user's Mac at `127.0.0.1:18187`. The Mac Studio stores references, drafts, job history, and finished MP4 files locally; a separate GPU API and ComfyUI render jobs when the GPU connection is available. The primary workflow is Create `/`, Story `/?view=scenes`, and Assets `/?view=assets`. Create also opens the same library as a reference picker. The old browser editor remains at `/editor/` only for saved projects; its preview-only effects do not export, so it is no longer a primary workspace.

## Capabilities and Constraints

- Create accepts image, video, and audio references, supports named reference mentions in prompts, and submits generation jobs to the local queue.
- The existing server contract exposes the MiniMax H3 model, duration, aspect ratio, quality, and advanced director and sampling settings.
- The user requested a default batch of four independently queued generations to compare real outputs. Each result must correspond to an actual job and MP4; the interface must not fabricate takes or preview media.
- Story, editing, and asset-management capabilities use separate workspaces and remain connected to their current implementations.
- GPU availability changes over time; local queue status must remain visible and must not imply that a render completed when it did not.

## Brand Commitments

The product is named H3 Studio (shell: TaleMondo). The CREATE screen keeps references, prompt, compact generation controls, a Generate action, and a results gallery. Main navigation is Create, Story, Edit, Assets. Russian is the default interface language.

## Evidence on Hand

- Product and architecture facts: `docs/README.md`, `docs/STUDIO_STRUCTURE.md`, and `AGENTS.md`.
- Existing create workflow and job queue: `preview/h3-director/index.html` and `preview/h3-director/app.mjs`.

## Product Principles

1. Each workspace centers one user task.
2. Generation controls submit real H3 jobs with validated settings.
3. Every displayed result represents a locally saved job and media file.
4. Keep local project data on the Mac and clearly show GPU availability.
5. Keep expert controls available without crowding the default creation flow.
