# Automatic LoRA routing in Mac Studio

For new Studio jobs, `style_adapter=auto` is resolved before the job is saved.
The resolved adapter and `lora_auto_reason` are immutable job settings and are
sent to the GPU API. The GPU reports an adapter only when its required files
are present in the ComfyUI loader and its name is enabled by
`H3_ENABLED_STYLE_ADAPTERS`. An unavailable adapter is never substituted by a
different category.

| Intent in prompt and mode | Adapter | Graph |
| --- | --- | --- |
| 3D animation | `style3d` | 3D LoRA, strength 1.0 |
| 2D animation | `cartoon2d` | 2D LoRA, strength 1.0 |
| Explicit realistic fight | `combat` | Wushu action 0.5, then spatial physics 0.3; `wushu_action` trigger |
| Explicit realistic human/face/skin detail | `realism_people` | realism people 0.7; `r34l1sm` trigger |
| Other prompt | `none` | native H3 only |

The combat strengths and trigger follow the [model author's card](https://huggingface.co/Jojocodex/minimax-h3-wushu-action-lora).
These are routing defaults, not evidence that a rendered result is better.
Camera/atmosphere embeddings and Director text controls are separate from LoRA
and are not selected by this router.

Before deployment, reconcile each active job with the GPU registry and ComfyUI
queue. The overlay installer fetches all required adapter weights and X2 Detail
VAE with pinned Hugging Face revisions and SHA-256 checks. It configures all four
adapter modes; `combat` requires both the Wushu action and spatial-physics files.
The installer checks that the ComfyUI queue is empty before it restarts ComfyUI.
Restart only `h3-api` for API-only changes; keep ComfyUI and Qwen running. Restart
Mac Studio only when its active job has reached a terminal state, and update the
Studio JS request to send `auto` for realistic scenes.
Check capabilities from GPU and Mac, create a *saved* job only after the user
asks for a new render, and verify the exact queued graph. Roll back by restoring
the backed-up source/config and restarting the two API services. Preserve all
job databases, outputs, media, and model files.

Validation before GPU execution: `scripts/check.sh` and a read-only ComfyUI
`LoraLoaderModelOnly` inventory. A real H3 render and visual review remain
necessary to establish compatibility and useful quality for the combat files.

## Fresh-server model pins

`scripts/install_h3_h200_overlay.sh` installs these models. Revisions are full
immutable Hugging Face commit IDs; SHA-256 is checked before a weight is
installed. The X2 path uses only the decoder node pack; the separate RGB
reference-detail enhancer is not required for the eye-comparison A/B variant.

| API mode / use | Repository @ revision | File | SHA-256 |
|---|---|---|---|
| 3D style (`style3d`) | `SOLRICKS/3D-Animation-Style-MiniMax-H3` @ `c06b8d8d44cb6ba0e5224549b3d243d1728f7019` | `3dsrx_minimax_h3_rank16-step00001250.safetensors` | `28b56d2240990436fba8d9a1fa06310c971a7ef95d3046059118445e0c0a4ef0` |
| 2D style (`cartoon2d`) | `prithivMLmods/MiniMax-H3-Rough-2D-Cartoon-Illustration` @ `fc40d010a2b44fd4caa4b750b6f503eca5d61577` | `minimax-h3-rough-2d-cartoon-illustration-1200.safetensors` | `8d49fe5abad6195f321fc7d741cc024e006ff891a40c67844834866bcdcf9e59` |
| Combat (`combat`) | `Jojocodex/minimax-h3-wushu-action-lora` @ `2fcd16cf77d1af1db41ea4e8b782a493a6ea87c8` | `old/wushu_action_h3_lora_v5_3000_fl2va_no-adaln.safetensors` (installed under API filename) | `136c16924f7413f0edb1c439f29c1e4c9da13d2cf07117c4e46556691193aa16` |
| Combat companion | `Jojocodex/minimax-h3-spatial-physics-lora` @ `2e1fcd55fe3c1ce1b02ede2c0e2279ee722fd632` | `wushu_spatial_physics_clean_3000_pruned.safetensors` | `7d14f3701560068e7004159c8b2a7278bd2dbfc9e5e3b60d0bc9aef6c049919d` |
| Face close-up A/B | `prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp` @ `c89656bd0cb71873133dd2c7a925f306a21c633c` | `minimax-h3-facial-realism-closeup-cp2000.safetensors` | `df2e588dee147f58d6344910ec758c50d2696761afe2294a0887235336a3d83a` |
| People realism (`realism_people`) | `fal/MiniMax-H3-Realism-People-LoRA` @ `b6e6b9f683c80c38684dbb1adcd566fc69a711fb` | `h3-realism-people-t2v-i2v-r2v.safetensors` | `acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e` |
| X2 Detail VAE (A/B only) | `speach1sdef178/MiniMax-H3-X2-Detail-VAE` @ `af8c92d267c6849fec5032c35a65d5766737b338` | `MiniMax-H3-X2-Detail-v1.safetensors` | `2296840f4acedcaa976688e7d7b97f7bf570b136e400385d3f46224011897aac` |

X2 decoder node pack: `TripleHeadedMonkey/ComfyUI-MiniMaxH3_LatentUpscaler`,
Git commit `2e568dfe3e4f5e81da178bc845e05dcfbb64d55b` (pinned checkout,
clean worktree). The pack provides `MiniMaxH3VAEDecodeFast`. X2 remains opt-in
through `ab_vae=x2detail`; base VAE decode remains unchanged.
