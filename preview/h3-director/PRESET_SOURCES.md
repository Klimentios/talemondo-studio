# Director presets and technical routing

See [assets/SOURCES.md](assets/SOURCES.md) for attribution, image licenses, and the official technical sources for the five camera cards. The cinematic atlas and color swatches are original visual explanations, not measurements or a promise of the generated result.

The 16 Director thumbnail scenes were generated for this Studio on 2026-10-03 and cropped into separate landscape images under `assets/director/tiles/`. They deliberately use distinct subjects. These UI images are not sent to H3 as reference assets. The prior atlas is backed up at `.codex-tmp/h3-design-backup-20261003/cinematic-atlas.png`.

The preset catalog is in `director-presets.mjs`. Every selection adds a concrete art-direction instruction to the submitted H3 prompt. Supported dolly, orbit, lateral tracking, and crane choices also build a CameraGuide v2 trajectory. Selected lens and focus settings set its optical keys. The optional `camera_optics` payload is for the H3OperatorCamera node when the server confirms that node is installed. If the node is unavailable, the server must reject that optical request before it enters the GPU queue; it must not silently pretend that the native control was applied.

Camera body specifications describe the physical reference camera. H3 does not become a physical simulation of that camera. Lighting, color, and style choices remain direction for H3 Director unless an independent graph input is explicitly implemented and verified on the deployed GPU. The 3D scenario keeps its existing LoRA routing and availability checks.

Motion preset previews are original illustrative stills. No third-party motion clip is attached to a job automatically. A user video remains an explicit Ref2VA reference.
