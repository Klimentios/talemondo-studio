"""Export evaluated world-space transforms and validate the previs scene."""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
CAMERA_NAME = "CAM_Main"
CHARACTER_NAME = "CHAR_A_ROOT"
PROXY_NAME = "CHAR_A"


def xyz(vector):
    return {axis: round(float(getattr(vector, axis)), 6) for axis in "xyz"}


def quaternion(value):
    return {axis: round(float(getattr(value, axis)), 6) for axis in "wxyz"}


def evaluated_world(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    return evaluated.matrix_world.copy(), evaluated


def sample_scene():
    scene = bpy.context.scene
    camera = bpy.data.objects.get(CAMERA_NAME)
    character = bpy.data.objects.get(CHARACTER_NAME)
    proxy = bpy.data.objects.get(PROXY_NAME)
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("CAM_Main camera is missing")
    if scene.camera != camera or camera.data.lens <= 0:
        raise RuntimeError("Scene camera or 35 mm lens is invalid")
    if character is None:
        raise RuntimeError("CHAR_A_ROOT is missing")
    if proxy is None or proxy.type != "MESH" or proxy.parent != character:
        raise RuntimeError("CHAR_A proxy block is missing or detached")
    if (scene.frame_start, scene.frame_end, scene.render.fps) != (1, 240, 24):
        raise RuntimeError("Expected timeline: frames 1-240 at 24 FPS")
    if (scene.render.resolution_x, scene.render.resolution_y) != (1280, 720):
        raise RuntimeError("Expected resolution: 1280x720")

    cameras, objects = [], []
    camera_positions, camera_rotations, character_positions = [], [], []
    previous_frame = scene.frame_current
    try:
        for frame in range(scene.frame_start, scene.frame_end + 1):
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            camera_matrix, evaluated_camera = evaluated_world(camera, depsgraph)
            character_matrix, _ = evaluated_world(character, depsgraph)
            proxy_matrix, evaluated_proxy = evaluated_world(proxy, depsgraph)
            camera_location = camera_matrix.translation.copy()
            camera_rotation = camera_matrix.to_quaternion()
            character_location = character_matrix.translation.copy()
            character_rotation = character_matrix.to_quaternion()
            proxy_corners = [proxy_matrix @ Vector(corner) for corner in evaluated_proxy.bound_box]
            proxy_size = Vector(tuple(
                max(corner[index] for corner in proxy_corners)
                - min(corner[index] for corner in proxy_corners)
                for index in range(3)
            ))
            camera_positions.append(camera_location)
            camera_rotations.append(camera_rotation)
            character_positions.append(character_location)
            cameras.append({
                "frame": frame,
                "time": round((frame - 1) / scene.render.fps, 6),
                "location": xyz(camera_location),
                "rotation_euler": xyz(camera_rotation.to_euler()),
                "rotation_quaternion": quaternion(camera_rotation),
                "lens_mm": round(float(evaluated_camera.data.lens), 6),
                "fov": round(float(evaluated_camera.data.angle_x), 6),
                "fov_vertical": round(float(evaluated_camera.data.angle_y), 6),
            })
            objects.append({
                "frame": frame,
                "time": round((frame - 1) / scene.render.fps, 6),
                "character": {
                    "location": xyz(character_location),
                    "rotation": xyz(character_rotation.to_euler()),
                },
                "proxy": {
                    "location": xyz(proxy_matrix.translation),
                    "rotation": xyz(proxy_matrix.to_quaternion().to_euler()),
                    "world_scale": xyz(proxy_matrix.to_scale()),
                    "world_bounds_size_m": xyz(proxy_size),
                },
            })
    finally:
        scene.frame_set(previous_frame)
    return cameras, objects, camera_positions, camera_rotations, character_positions


def motion_stats(positions):
    steps = [(b - a).length for a, b in zip(positions, positions[1:])]
    return sum(steps), max(steps, default=0.0)


def validate_motion(camera_positions, camera_rotations, character_positions):
    camera_travel, camera_max_step = motion_stats(camera_positions)
    character_travel, character_max_step = motion_stats(character_positions)
    angle_steps = [
        math.degrees(a.rotation_difference(b).angle)
        for a, b in zip(camera_rotations, camera_rotations[1:])
    ]
    max_angle = max(angle_steps, default=0.0)
    print("CAMERA VALIDATION")
    print(f"Total travel: {camera_travel:.2f} meters")
    print(f"Maximum frame-to-frame translation: {camera_max_step:.3f} meters")
    print(f"Maximum frame-to-frame rotation: {max_angle:.2f} degrees")
    if camera_travel < 0.5:
        raise RuntimeError("Camera trajectory has insufficient movement")
    if camera_max_step > 0.20 or max_angle > 5.0:
        print("WARNING: POSSIBLE CAMERA JUMP")
        raise RuntimeError("Camera movement exceeds smoothness limits")
    print("CHARACTER VALIDATION")
    print(f"Total travel: {character_travel:.2f} meters")
    print(f"Maximum frame-to-frame movement: {character_max_step:.3f} meters")
    print(f"Start position: {xyz(character_positions[0])}")
    print(f"Final position: {xyz(character_positions[-1])}")
    if character_travel < 2:
        raise RuntimeError("Character is not moving through the room")
    if character_max_step > 0.20:
        print("WARNING: POSSIBLE CHARACTER TELEPORT")
        raise RuntimeError("Character movement exceeds smoothness limits")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_all():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cameras, objects, camera_positions, camera_rotations, character_positions = sample_scene()
    validate_motion(camera_positions, camera_rotations, character_positions)
    scene = bpy.context.scene
    write_json(OUTPUT / "camera.json", cameras)
    write_json(OUTPUT / "objects.json", objects)
    static_names = ["PROP_Table", "PROP_Chair", "PROP_Cabinet"]
    static_transforms = {}
    scene.frame_set(scene.frame_start)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for name in static_names:
        obj = bpy.data.objects[name]
        matrix, evaluated = evaluated_world(obj, depsgraph)
        static_transforms[name] = {
            "location": xyz(matrix.translation),
            "rotation": xyz(matrix.to_quaternion().to_euler()),
            "dimensions_m": xyz(evaluated.dimensions),
        }
    write_json(OUTPUT / "scene.json", {
        "fps": scene.render.fps,
        "frames": scene.frame_end - scene.frame_start + 1,
        "duration": (scene.frame_end - scene.frame_start + 1) / scene.render.fps,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "camera": CAMERA_NAME,
        "characters": [CHARACTER_NAME],
        "static_objects": static_names,
        "static_object_transforms": static_transforms,
        "motion_events": [
            {"frame": 1, "event": "doorway_pause"},
            {"frame": 24, "event": "enter_room"},
            {"frame": 144, "event": "pass_table"},
            {"frame": 192, "event": "approach_chair"},
            {"frame": 216, "event": "begin_sit"},
            {"frame": 240, "event": "seated"},
        ],
    })
    print(f"Exported 240 evaluated camera and character samples to {OUTPUT}")


if __name__ == "__main__":
    export_all()
