"""Build, validate, export and render a 10-second Blender motion previs."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
FPS = 24
FRAME_START = 1
FRAME_END = 240
WIDTH = 1280
HEIGHT = 720
ROOM_WIDTH = 7.0
ROOM_LENGTH = 9.0
ROOM_HEIGHT = 3.0
CAMERA_LENS = 35.0


def material(name: str, rgb: tuple[float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1.0)
    return mat


def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def configure_scene():
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.frame_start = FRAME_START
    scene.frame_end = FRAME_END
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    # Workbench renders proxy geometry quickly and consistently in background mode.
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "BOTH"
    scene.display.shading.background_type = "WORLD"
    scene.display.shading.background_color = (0.72, 0.72, 0.72)
    scene.render.film_transparent = False
    scene.world.color = (0.5, 0.5, 0.5)
    scene.view_settings.view_transform = "Standard"


def cube(name, location, dimensions, mat, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse.identity()
    return obj


def empty(name, parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.empty_display_size = 0.15
    if parent:
        obj.parent = parent
    return obj


def create_environment(mats):
    half_width, half_length = ROOM_WIDTH / 2, ROOM_LENGTH / 2
    cube("ENV_Floor", (0, 0, -0.06), (ROOM_WIDTH, ROOM_LENGTH, 0.12), mats["floor"])
    # Doorway in back wall: 1.0 m clear width, 2.1 m clear height.
    side_width = (ROOM_WIDTH - 1.0) / 2
    cube("ENV_Wall_Back_Left", (-(0.5 + side_width / 2), half_length, ROOM_HEIGHT / 2),
         (side_width, 0.12, ROOM_HEIGHT), mats["wall"])
    cube("ENV_Wall_Back_Right", ((0.5 + side_width / 2), half_length, ROOM_HEIGHT / 2),
         (side_width, 0.12, ROOM_HEIGHT), mats["wall"])
    cube("ENV_Wall_Back_Lintel", (0, half_length, 2.55), (1.0, 0.12, 0.9), mats["wall"])
    # Named empty represents the wall assembly; the actual mesh pieces make the opening.
    back = empty("ENV_Wall_Back")
    for name in ("ENV_Wall_Back_Left", "ENV_Wall_Back_Right", "ENV_Wall_Back_Lintel"):
        bpy.data.objects[name].parent = back
    cube("ENV_Wall_Left", (-half_width, 0, ROOM_HEIGHT / 2),
         (0.12, ROOM_LENGTH, ROOM_HEIGHT), mats["wall"])
    cube("ENV_Wall_Right", (half_width, 0, ROOM_HEIGHT / 2),
         (0.12, ROOM_LENGTH, ROOM_HEIGHT), mats["wall"])
    cube("PROP_Table", (-0.9, 0.25, 0.7125), (2.0, 0.8, 0.075), mats["table"])
    for sx in (-1.78, -0.02):
        for sy in (-0.06, 0.56):
            cube(f"PROP_Table_Leg_{sx}_{sy}", (sx, sy, 0.34), (0.08, 0.08, 0.68), mats["table"])
    cube("PROP_Chair", (1.4, -1.5, 0.46), (0.56, 0.56, 0.08), mats["chair"])
    cube("PROP_Chair_Back", (1.4, -1.19, 0.81), (0.56, 0.08, 0.72), mats["chair"])
    for sx in (1.17, 1.63):
        for sy in (-1.73, -1.27):
            cube(f"PROP_Chair_Leg_{sx}_{sy}", (sx, sy, 0.21), (0.06, 0.06, 0.42), mats["chair"])
    cube("PROP_Cabinet", (-2.5, 3.35, 1.0), (1.5, 0.5, 2.0), mats["cabinet"])


def create_character_proxy(mats):
    root = empty("CHAR_A_ROOT")
    proxy = cube("CHAR_A", (0, 0, 0.9), (0.45, 0.32, 1.8), mats["body"], root)
    # A colored face makes heading visible without any character rig.
    proxy.data.materials.append(mats["facing"])
    for face in proxy.data.polygons:
        if face.normal.y < -0.5:
            face.material_index = 1
    return root


def keyframe(obj, frame, location=None, rotation=None, scale=None):
    if location is not None:
        obj.location = location
        obj.keyframe_insert(data_path="location", frame=frame)
    if rotation is not None:
        obj.rotation_euler = rotation
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    if scale is not None:
        obj.scale = scale
        obj.keyframe_insert(data_path="scale", frame=frame)


def animate_character(root):
    # Every beat uses world-space root motion. Positions remain continuous.
    path = [
        (1, (0.0, 4.83, 0.0), 0),
        (24, (0.0, 4.83, 0.0), 0),
        (96, (1.0, 1.25, 0.0), math.radians(4)),
        (144, (1.95, -0.2, 0.0), math.radians(38)),
        (192, (2.2, -2.2, 0.0), math.radians(-40)),
        (216, (1.4, -2.1, 0.0), 0),
        (240, (1.4, -1.5, 0.0), 0),
    ]
    for frame, position, heading in path:
        keyframe(root, frame, position, (0, 0, heading))
    proxy = bpy.data.objects["CHAR_A"]
    keyframe(proxy, 1, location=(0, 0, 0.9), scale=(1, 1, 1))
    keyframe(proxy, 216, location=(0, 0, 0.9), scale=(1, 1, 1))
    # Simple pose marker: the box settles onto the 0.5 m chair seat.
    keyframe(proxy, 240, location=(0, 0, 0.95), scale=(1, 1, 0.5))


def create_camera():
    rig = empty("CAM_Rig")
    target = empty("CAM_Target")
    camera_data = bpy.data.cameras.new("CAM_Main_Data")
    camera_data.lens = CAMERA_LENS
    camera_data.sensor_fit = "HORIZONTAL"
    camera = bpy.data.objects.new("CAM_Main", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.parent = rig
    camera.location = (0, 0, 0)
    track = camera.constraints.new(type="TRACK_TO")
    track.name = "Track_CAM_Target"
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    bpy.context.scene.camera = camera
    return rig, target


def animate_camera(rig, target):
    # Dolly back, orbit right around the action, then a 0.6 m push-in.
    for frame, position in (
        (1, (0.0, -5.9, 2.15)), (48, (0.0, -5.87, 2.15)),
        (120, (0.0, -7.17, 2.20)), (192, (3.0, -5.45, 2.13)),
        (240, (2.73, -4.91, 2.05)),
    ):
        keyframe(rig, frame, position)
    for frame, position in (
        (1, (0.15, 3.05, 1.12)), (48, (0.35, 2.7, 1.10)),
        (120, (0.83, 0.57, 1.08)), (192, (1.8, -1.7, 1.06)),
        (240, (1.4, -1.5, 1.04)),
    ):
        keyframe(target, frame, position)


def smooth_curves():
    for obj in bpy.data.objects:
        if not obj.animation_data or not obj.animation_data.action:
            continue
        for curve in obj.animation_data.action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = "BEZIER"
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"


def configure_render():
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.ffmpeg.ffmpeg_preset = "REALTIME"
    scene.render.filepath = str(OUTPUT / "previs.mp4")


def main():
    try:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        reset_scene()
        configure_scene()
        mats = {
            "floor": material("MAT_Floor", (0.69, 0.69, 0.69)),
            "wall": material("MAT_Wall", (0.78, 0.78, 0.78)),
            "table": material("MAT_Table", (0.22, 0.24, 0.27)),
            "chair": material("MAT_Chair", (0.40, 0.43, 0.47)),
            "cabinet": material("MAT_Cabinet", (0.37, 0.38, 0.39)),
            "body": material("MAT_Character", (0.18, 0.37, 0.47)),
            "facing": material("MAT_Facing", (0.93, 0.48, 0.18)),
        }
        create_environment(mats)
        character = create_character_proxy(mats)
        animate_character(character)
        rig, target = create_camera()
        animate_camera(rig, target)
        smooth_curves()
        configure_render()
        scene = bpy.context.scene
        scene.frame_set(FRAME_START)
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "scene.blend"))
        sys.path.insert(0, str(ROOT))
        from export_metadata import export_all
        from render_scene import render_all
        export_all()
        render_all()
    except Exception as exc:
        print(f"PREVIS BUILD FAILED: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
