import unittest

from app.h3_ir import (Reference, SECTIONS, build_skeleton, is_blocking, parse_ir, render_ir, scenarist_messages,
                       shot_starts, validate_ir)


def recipe_refs():
    # Shape of the picture-only golden job: one full character, one face, one location.
    return [Reference("image", 1, "Персонаж целиком", "ADAM"),
            Reference("image", 2, "Лицо героя", "MARTIN"),
            Reference("image", 3, "Окружение")]


def good_ir(skeleton, headers, words=400):
    filler = " ".join(["The warm lamp light falls across the worn sofa cushions."] * (words // 10))
    retention = "\n".join(line.replace("[SHOTS]", "[Shot 1], [Shot 2]") for line in skeleton.retention_lines)
    return render_ir({
        "subject_definitions": "\n".join(skeleton.subject_lines),
        "summary": f"[{' + '.join(skeleton.task_types)}] <Subject 1> sleeps while <Subject 2> walks in <Subject 3>.",
        "retention_analysis": retention,
        "detailed_description": (
            "The target video is live-action with soft practical light.\n"
            f"{headers[0]} A wide shot shows <Subject 3>. <Subject 1> sleeps on the sofa. {filler}\n"
            f"{headers[1]} the shot cuts to a close-up of <Subject 2> (S1), who says, <d>[Russian] Тише.</d> "
            "He closes his lips and the camera pushes in with small amplitude at slow speed."),
        "overall_soundscape": "Quiet room tone and a ticking wall clock continue throughout.",
        "non_diegetic_music": "N/A",
    })


class SkeletonTests(unittest.TestCase):
    def test_recipe_like_job(self):
        sk = build_skeleton(recipe_refs())
        self.assertEqual(sk.subject_lines, [
            "<Subject 1> is ADAM, whose full-body appearance and silhouette come from <Picture 1>.",
            "<Subject 2> is MARTIN, whose face and identity come from <Picture 2>.",
            "<Subject 3> is the environment in <Picture 3>.",
        ])
        self.assertEqual(sk.task_types, ["reference generation"])
        self.assertTrue(all(":" in line and "_" in line for line in sk.retention_lines))

    def test_movement_video_sits_inside_the_hero_and_camera_is_structural(self):
        sk = build_skeleton([Reference("image", 1, "Персонаж целиком", "VEGA"),
                             Reference("video", 1, "Движение и камера", "VEGA")])
        self.assertIn("<Subject 1> is VEGA, whose full-body appearance and silhouette come from <Picture 1> "
                      "and whose body motion, action timing and facial performance is transferred from the separate performer in <Video 1>.",
                      sk.subject_lines)
        self.assertIn("<Video 1> (body motion, action timing and facial performance): attribute_transfer - only the performer's body motion, "
                      "action timing and facial performance are transferred to <Subject 1>; VEGA's hair, facial hair, face, build and "
                      "clothing stay as in <Picture 1>.", sk.retention_lines)
        self.assertTrue(any("including hairstyle, hair length, hair color and facial hair" in line
                            for line in sk.retention_lines))
        self.assertIn("<Video 1> is the reference for the target video's camera, framing, scene layout and lighting.",
                      sk.subject_lines)

    def test_unowned_motion_video_and_voice(self):
        sk = build_skeleton([Reference("image", 1, "Лицо героя", "VEGA"),
                             Reference("video", 1, "Движение"),
                             Reference("audio", 1, "Голос", "VEGA")], speaking_owners=["VEGA"])
        self.assertIn("<Video 1> is the reference for the target video's body motion, action timing and facial performance.", sk.subject_lines)
        self.assertTrue(any(line.startswith("<Video 1> (body motion, action timing and facial performance): attribute_transfer")
                            for line in sk.retention_lines))
        self.assertIn("<Audio 1> is the voice-timbre reference for <Subject 1> (S1).", sk.subject_lines)
        self.assertEqual(sk.task_types, ["reference generation", "audio reference"])

    def test_scene_frame_is_keyframe_completion(self):
        sk = build_skeleton([Reference("image", 1, "Кадр сцены")])
        self.assertEqual(sk.subject_lines, ["<Picture 1> is the first frame of [Shot 1]."])
        self.assertEqual(sk.task_types[0], "keyframe completion")


class PropTests(unittest.TestCase):
    def test_prop_becomes_its_own_subject(self):
        sk = build_skeleton([Reference("image", 1, "Предмет")])
        self.assertEqual(sk.subject_lines, ["<Subject 1> is the object in <Picture 1>."])


class ShotTests(unittest.TestCase):
    def test_headers_from_cut_lengths(self):
        self.assertEqual(shot_starts([5, 4.5, 6]), ["[Shot 1]", "[Shot 2] At 00:05.000,", "[Shot 3] At 00:09.500,"])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.sk = build_skeleton(recipe_refs(), speaking_owners=["MARTIN"])
        self.headers = shot_starts([7, 8])

    def test_good_ir_passes_and_roundtrips(self):
        text = good_ir(self.sk, self.headers)
        self.assertEqual(validate_ir(text, self.sk, self.headers), [])
        self.assertEqual(list(parse_ir(text)), list(SECTIONS))

    def test_format_errors_are_reported(self):
        text = good_ir(self.sk, self.headers, words=60).replace("detailed_description:\n", "detailed_description:\n<Subject 9> waves. ", 1)
        text = text.replace("Quiet room tone", "Тихо, no music, no people, no talking, never loud")
        problems = " | ".join(validate_ir(text, self.sk, self.headers))
        self.assertIn("<Subject 9>", problems)
        self.assertIn("слов", problems)
        self.assertNotIn("русский текст вне", problems)

    def test_russian_description_soundscape_and_music_are_blocked(self):
        # Rule of 04.11.2026 (user): descriptive sections only in English; was accepted verbatim before.
        sections = parse_ir(good_ir(self.sk, self.headers))
        sections['detailed_description'] = f"{self.headers[0]} " + ' '.join(['Герой спокойно идёт по коридору и слышит тихие шаги.'] * 45) + f" {self.headers[1]} Герой говорит <d>[Russian] Тише.</d>"
        sections['overall_soundscape'] = 'Тихие шаги, шелест ткани и гул помещения.'
        sections['non_diegetic_music'] = 'Без музыки.'
        problems = [p for p in validate_ir(render_ir(sections), self.sk, self.headers) if is_blocking(p)]
        self.assertEqual(sorted(p.split(':')[0] for p in problems),
                         ['detailed_description', 'non_diegetic_music', 'overall_soundscape'])

    def test_negations_and_dialogue_language(self):
        text = good_ir(self.sk, self.headers).replace(
            "The target video is", "No people, no cars, do not show text, never zoom. The target video is").replace(
            "<d>[Russian] Тише.</d>", "<d>Тише.</d>")
        problems = " | ".join(validate_ir(text, self.sk, self.headers))
        self.assertIn("отрицаний", problems)
        self.assertIn("реплика без языка", problems)

    def test_scenarist_request_carries_skeleton_and_headers(self):
        messages = scenarist_messages("Адам спит, Мартин ходит по квартире.", self.sk, self.headers)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("<Subject 2> is MARTIN", messages[1]["content"])
        self.assertIn("[Shot 2] At 00:07.000,", messages[1]["content"])
        self.assertIn("MARTIN".casefold() + " = (S1)", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()


class SubjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.sk = build_skeleton([Reference("image", 1, "Лицо героя", "Адам 1"), Reference("image", 2, "Костюм", "Адам 1"),
                                  Reference("image", 3, "Лицо героя", "Анна 2"), Reference("image", 4, "Костюм", "Анна 2")])
        self.sk.labels.update({"<Picture 1>", "<Picture 2>", "<Picture 3>", "<Picture 4>"})

    def problems(self, definitions):
        text = ("subject_definitions:\n" + definitions + "\n\nsummary:\n[reference generation] x\n\nretention_analysis:\n"
                "<Subject 1>: fully_preserved - x\n<Subject 2>: fully_preserved - x\n\ndetailed_description:\nx\n\n"
                "overall_soundscape:\nx\n\nnon_diegetic_music:\nN/A")
        from app.h3_ir import is_blocking
        return [p for p in validate_ir(text, self.sk, []) if is_blocking(p)]

    def test_english_names_and_own_numbering_are_fine(self):
        self.assertEqual(self.problems(
            "<Subject 1> is Anna, whose face comes from <Picture 3> and whose costume comes from <Picture 4>.\n"
            "<Subject 2> is Adam, whose face comes from <Picture 1> and whose costume comes from <Picture 2>."), [])

    def test_wrong_picture_numbers_are_blocked_with_a_clear_reason(self):
        problems = " | ".join(self.problems(
            "<Subject 1> is Anna, whose face and costume come from <Picture 1>.\n"
            "<Subject 2> is Adam, whose face and costume come from <Picture 2>."))
        self.assertIn("картинки героя Anna 2 (<Picture 3>, <Picture 4>) не упомянуты", problems)
        self.assertIn("картинки героя Adam 1 разнесены", problems)

    def test_mixing_two_heroes_in_one_subject_is_blocked(self):
        problems = " | ".join(self.problems(
            "<Subject 1> is Anna, whose face comes from <Picture 3> and whose costume comes from <Picture 2>.\n"
            "<Subject 2> is Adam, whose face comes from <Picture 1> and whose costume comes from <Picture 4>."))
        self.assertIn("смешивает картинки разных героев", problems)


class RebindPicturesTests(unittest.TestCase):
    def test_named_heroes_get_their_own_pictures(self):
        from app.h3_ir import is_blocking, rebind_subject_pictures
        sk = build_skeleton([Reference("image", 1, "Лицо героя", "Anna"), Reference("image", 2, "Костюм", "Anna"),
                             Reference("image", 3, "Лицо героя", "adam"), Reference("image", 4, "Костюм", "adam")])
        sk.labels.update({f"<Picture {i}>" for i in range(1, 5)})
        text = ("subject_definitions:\n<Subject 1> is Anna, whose face and costume come from <Picture 1>.\n"
                "<Subject 2> is Adam, whose face and costume come from <Picture 2>.\n\nsummary:\n[reference generation] x\n\n"
                "retention_analysis:\n<Subject 1>: fully_preserved - Anna from <Picture 1>.\n"
                "<Subject 2>: fully_preserved - Adam from <Picture 2>.\n\ndetailed_description:\nx\n\n"
                "overall_soundscape:\nx\n\nnon_diegetic_music:\nN/A")
        fixed = rebind_subject_pictures(text, sk)
        self.assertIn("<Subject 2> is Adam, whose face and identity come from <Picture 3> and whose costume and carried props come from <Picture 4>.", fixed)
        self.assertIn("<Subject 1> is Anna, whose face and identity come from <Picture 1> and whose costume and carried props come from <Picture 2>.", fixed)
        self.assertIn("<Subject 2>: fully_preserved - Adam from <Picture 3>.", fixed)
        self.assertEqual([p for p in validate_ir(fixed, sk, []) if is_blocking(p)], [])

    def test_retention_analysis_uses_role_sources_after_rebinding(self):
        from app.h3_ir import rebind_subject_pictures
        sk = build_skeleton([Reference("image", 1, "Лицо героя", "Anna"),
                             Reference("image", 2, "Костюм", "Anna"),
                             Reference("image", 3, "Лицо героя", "Adam"),
                             Reference("image", 4, "Костюм", "Adam")])
        sk.labels.update({f"<Picture {i}>" for i in range(1, 5)})
        text = ("subject_definitions:\n"
                "<Subject 1> is Anna, whose face, identity and complete costume come from <Picture 1>.\n"
                "<Subject 2> is Adam, whose face, identity and complete costume come from <Picture 2>.\n\n"
                "summary:\n[reference generation] x\n\n"
                "retention_analysis:\n"
                "<Subject 1> (appears in [Shot 1]): fully_preserved - Anna's complete costume comes from <Picture 1>.\n"
                "<Subject 2> (appears in [Shot 1]): fully_preserved - Adam's complete costume comes from <Picture 2>.\n\n"
                "detailed_description:\nx\n\noverall_soundscape:\nx\n\nnon_diegetic_music:\nN/A")

        fixed = rebind_subject_pictures(text, sk)

        subjects = fixed.split("summary:\n", 1)[0]
        adam_subject = next(line for line in subjects.splitlines() if line.startswith("<Subject 2>"))
        self.assertIn("face and identity come from <Picture 3>", adam_subject)
        self.assertIn("costume and carried props come from <Picture 4>", adam_subject)
        self.assertNotIn("complete costume come from <Picture 3>", adam_subject)
        retention = fixed.split("retention_analysis:\n", 1)[1].split("\n\ndetailed_description:", 1)[0]
        adam_line = next(line for line in retention.splitlines() if line.startswith("<Subject 2>"))
        self.assertIn("<Picture 3>", adam_line)
        self.assertIn("<Picture 4>", adam_line)
        self.assertNotIn("complete costume comes from <Picture 3>", adam_line)

    def test_unknown_names_are_left_for_the_check(self):
        from app.h3_ir import rebind_subject_pictures
        sk = build_skeleton([Reference("image", 1, "Лицо героя", "Anna")])
        text = "subject_definitions:\n<Subject 1> is Mira, whose face comes from <Picture 1>.\n\nsummary:\nx"
        self.assertEqual(rebind_subject_pictures(text, sk), text)


class MotionRetentionPolicyTests(unittest.TestCase):
    def test_gpu_prompt_binds_two_heroes_scale_and_clean_scene(self):
        from app.motion_reference_policy import apply_cast_and_clean_scene_contract
        prompt = 'detailed_description:\n[Shot 1] Two cats dance.\n\noverall_soundscape:\nRoom tone.'
        videos = [{'role': 'Движение и камера', 'performers': [
            {'owner': 'VEGA', 'performer': 'left cat'},
            {'owner': 'ORION', 'performer': 'right cat', 'relative_scale_percent': 67},
        ]}]
        result = apply_cast_and_clean_scene_contract(prompt, videos)
        self.assertIn('ORION remains 67% of VEGA', result)
        self.assertIn('exactly one instance', result)
        self.assertIn('not an additional visible cast', result)
        self.assertIn('background, reflected, transitional or partial duplicates', result)
        self.assertIn('no source logos', result)
        self.assertEqual(apply_cast_and_clean_scene_contract(prompt, [{'role': 'Исходное видео'}]), prompt)

    def test_all_motion_roles_remove_source_appearance_retention_without_changing_action(self):
        from app.motion_reference_policy import normalize_motion_retention
        prompt = ("retention_analysis:\n<Picture 1>: fully_preserved - target haircut.\n"
                  "<Video 1>: partially_preserved - original performer.\n"
                  "<Video 1>: lighting_reference - light.\n"
                  "<Video 2>: fully_preserved - another source.\n\ndetailed_description:\n[Shot 1] Turn left.\n")
        for role in ('Движение', 'Движение и камера', 'motion', 'motion_camera'):
            out = normalize_motion_retention(prompt, [{'role': role}, {'role': 'Исходное видео'}])
            self.assertNotIn('partially_preserved', out)
            self.assertEqual(out.count('<Video 1>:'), 1)
            self.assertIn('Hair motion follows that target hairstyle', out)
            self.assertIn('<Video 2>: fully_preserved - another source.', out)
            self.assertTrue(out.endswith('detailed_description:\n[Shot 1] Turn left.\n'))
            self.assertEqual(normalize_motion_retention(out, [{'role': role}, {'role': 'Исходное видео'}]), out)

    def test_photo_and_source_edit_prompts_stay_byte_identical(self):
        from app.motion_reference_policy import normalize_motion_retention
        prompt = "retention_analysis:\n  original\n\n\ndetailed_description:\ntext\n"
        self.assertEqual(normalize_motion_retention(prompt, []), prompt)
        self.assertEqual(normalize_motion_retention(prompt, [{'role': 'Исходное видео'}]), prompt)


# --- 04.11.2026: формат Ref2VA и английский язык до очереди -------------------------------
from app.h3_ir import foreign_format_problem, language_problems

_I2VA = ("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
         "integrated_multimodal_description: [Shot 1] VEGA walks.\n\noverall_soundscape: Room tone.")


def test_i2va_prompt_is_rejected_for_ref2va():
    assert "I2VA" in foreign_format_problem(_I2VA, "h3_ir")
    assert "I2VA" in foreign_format_problem(_I2VA, None)


def test_declared_ir_without_sections_is_rejected():
    assert foreign_format_problem("VEGA walks in the kitchen.", "h3_ir")


def test_plain_prompt_and_official_ir_pass_format_gate():
    assert foreign_format_problem("VEGA walks in the kitchen.", None) is None
    assert foreign_format_problem("subject_definitions:\n<Subject 1> is VEGA.", "h3_ir") is None


def test_cyrillic_outside_dialogue_blocks():
    text = ("subject_definitions:\n<Subject 1> is VEGA.\n\nsummary:\n[reference generation] Вега идёт.\n\n"
            "detailed_description:\n[Shot 1] VEGA says, <d>[Russian] Привет</d> and holds a sign «Касса».")
    problems = language_problems(text)
    assert len(problems) == 1 and problems[0].startswith("summary:")


def test_english_ir_with_russian_dialogue_has_no_language_problem():
    text = "subject_definitions:\n<Subject 1> is VEGA.\n\ndetailed_description:\n[Shot 1] <d>[Russian] Привет</d>"
    assert language_problems(text) == []
