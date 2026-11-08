"""H3 post nodes installed by scripts/install_h3_h200_overlay.sh into custom_nodes/h3-color-grade.

H3ColorGrade — color of the «Цвет» preset photo applied to the whole clip (Lab, one correction).
H3FilmLook   — contrast curve and moving grain from the «Камера» preset (film 35/16 mm, vintage…).
"""
from .film import film_look
from .grade import grade_clip


class H3ColorGrade:
    @classmethod
    def INPUT_TYPES(cls):
        number = lambda default, lo, hi: ("FLOAT", {"default": default, "min": lo, "max": hi, "step": 0.01})
        return {"required": {
            "images": ("IMAGE",),
            "target_l_mean": number(50.0, 0.0, 100.0), "target_a_mean": number(0.0, -128.0, 127.0), "target_b_mean": number(0.0, -128.0, 127.0),
            "target_l_std": number(20.0, 0.0, 100.0), "target_a_std": number(10.0, 0.0, 128.0), "target_b_std": number(10.0, 0.0, 128.0),
            "strength": number(0.5, 0.0, 1.0),
            "preserve_luminance": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "grade"
    CATEGORY = "MiniMax H3"
    DESCRIPTION = "Цветокор ролика по фото-референсу (Lab, единая коррекция на весь клип)."

    def grade(self, images, target_l_mean, target_a_mean, target_b_mean,
              target_l_std, target_a_std, target_b_std, strength, preserve_luminance):
        return (grade_clip(images, (target_l_mean, target_a_mean, target_b_mean),
                           (target_l_std, target_a_std, target_b_std), strength, preserve_luminance),)


class H3FilmLook:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "contrast": ("FLOAT", {"default": 0.0, "min": -0.5, "max": 0.5, "step": 0.01}),
            "grain": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            "grain_size": ("FLOAT", {"default": 1.2, "min": 1.0, "max": 3.0, "step": 0.1}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "apply"
    CATEGORY = "MiniMax H3"
    DESCRIPTION = "Плёночный характер камеры: контрастная кривая и живое зерно."

    def apply(self, images, contrast, grain, grain_size, seed):
        return (film_look(images, contrast, grain, grain_size, seed),)


NODE_CLASS_MAPPINGS = {"H3ColorGrade": H3ColorGrade, "H3FilmLook": H3FilmLook}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ColorGrade": "H3 Color Grade (photo reference)", "H3FilmLook": "H3 Film Look (camera)"}
