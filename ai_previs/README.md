# Blender AI previs

This project builds a 10 second spatial and motion reference: a proxy block enters
through a 1 m doorway, passes a table, approaches a chair, and sits. One camera
performs a restrained dolly back, right orbit, and push-in. The room and furniture
are static proxy geometry.

## Requirements

- Blender 4.1.1 with FFmpeg/H.264 support was tested on macOS. Other versions are unverified.
- No Python packages or manually prepared `.blend` files are required.

## Build, export, and render

From this directory:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python build_scene.py
```

If `blender` is on `PATH`, use `blender --background --python-exit-code 1 --python build_scene.py`.
The build replaces generated files in `output/`; preserve any hand edits before
running it again. It saves `scene.blend`, validates evaluated animation, exports
metadata, renders `previs.mp4`, and copies the video into `output/minimax/`.

To re-export metadata or re-render a saved scene:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background output/scene.blend --python-exit-code 1 --python export_metadata.py
/Applications/Blender.app/Contents/MacOS/Blender --background output/scene.blend --python-exit-code 1 --python render_scene.py
```

## Files

| File | Purpose |
| --- | --- |
| `output/scene.blend` | Editable camera, proxy scene, and animation |
| `output/previs.mp4` | 1280×720 H.264, 24 FPS, 240 frames |
| `output/camera.json` | Evaluated world location, Euler/quaternion rotation, lens, and horizontal FOV for each frame |
| `output/objects.json` | Evaluated world root and proxy block transforms for each frame |
| `output/scene.json` | Timeline, static object placements, and motion events |
| `output/minimax/reference_video.mp4` | Copy of the Blender reference video for MiniMax |
| `output/minimax/prompt.txt` | Reference-role guidance; add the desired appearance before generation |

`fov` is the horizontal angle in radians (`angle_x`); `fov_vertical` is `angle_y`.
Time for frame 1 is
0 seconds; frame 240 is sampled at 239/24 seconds and completes the 10 second
clip. JSON values come from Blender's evaluated dependency graph, including
parenting and tracking constraints. The JSON files are local pipeline data and
are not intended as MiniMax uploads. The proxy's front face is colored to show
heading; its height change at the end marks the sitting beat.

The viewport uses Blender Workbench material colors and studio lighting for
fast, readable geometry. Workbench does not use scene lights. This is a motion
blueprint, not an appearance or identity reference. Review the rendered video
before using it as a generation reference.
