"""scripts/h3_ab.py: variant form fields and the report (the network path runs in infra/h3_ab_stand.sh)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from local_studio.worker import gpu_form_fields

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h3_ab", ROOT / "scripts/h3_ab.py")
h3_ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h3_ab)


class HarnessTests(unittest.TestCase):
    def test_golden_job_and_baseline_fields_are_the_studio_submission(self):
        job, files = h3_ab.load_golden_job()
        self.assertEqual(len(files), 3)
        fields = h3_ab.variant_fields(job, "baseline", {"schema_version": 1})
        studio = gpu_form_fields(job)
        self.assertEqual(fields["speed"], "quality")
        self.assertEqual(studio["speed"], "quality", "Studio must not inherit a Turbo GPU default")
        self.assertEqual({k: v for k, v in fields.items() if k not in {"client_job_id", "ab_variant", "speed"}},
                         {k: v for k, v in studio.items() if k not in {"client_job_id", "speed"}})
        self.assertEqual(fields["ab_variant"], "baseline")
        self.assertNotIn("ab_director_plan_json", fields, "the baseline is planned, never replayed")

    def test_variants_replay_the_baseline_plan_and_add_only_their_option(self):
        job, _ = h3_ab.load_golden_job()
        plan = {"director_fallback": "x"}
        fields = h3_ab.variant_fields(job, "realism-0.6", plan)
        self.assertEqual(json.loads(fields["ab_director_plan_json"]), plan)
        extra = {k: v for k, v in fields.items() if k.startswith("ab_")}
        self.assertEqual(extra, {"ab_variant": "realism-0.6", "ab_lora": "realism_people",
                                 "ab_lora_strength": "0.6", "ab_director_plan_json": json.dumps(plan)})

    def test_report_marks_different_prompts_and_merges_runs(self):
        def row(index, name, prompt_hash, video=None):
            return {"index": index, "name": name, "fields": h3_ab.VARIANTS[name], "status": "success" if video else "error",
                    "steps": 25, "video": video, "error": None if video else "boom",
                    "timing": {"total_seconds": 100.0, "phases": {"sampling": 50.0}},
                    "vram": {"peak_used_gb": 61.2, "total_gb": 80.0, "samples": 3},
                    "ab": {"base_prompt_sha256": prompt_hash, "graph_sha256": "g" * 64}}

        settings = {"width": 640, "height": 1152, "length": 362, "steps": 25, "seed": 1}
        with tempfile.TemporaryDirectory() as temp:
            old, new = Path(temp, "old"), Path(temp, "new")
            for path in (old, new):
                path.mkdir()
            h3_ab.save(old, {"created": "t0", "tag": "comfy-a", "gpu_url": "u", "source": "golden",
                             "settings": settings, "variants": [row(1, "baseline", "a" * 64, "01-baseline.mp4")]})
            current = {"created": "t1", "tag": "comfy-b", "gpu_url": "u", "source": "golden", "settings": settings,
                       "variants": [row(1, "baseline", "a" * 64, "01-baseline.mp4"), row(2, "beta", "b" * 64)]}
            h3_ab.write_report(new, current, [h3_ab.load_results(old)])
            report = (new / "report.md").read_text(encoding="utf-8")
            page = (new / "index.html").read_text(encoding="utf-8")
        self.assertIn("ВНИМАНИЕ: промпты вариантов различаются", report)
        self.assertIn("| 2.0 |", report)  # 50 s sampling / 25 steps
        self.assertIn("comfy-a:baseline", report)
        self.assertIn('src="../old/01-baseline.mp4"', page)
        self.assertIn('src="01-baseline.mp4"', page)
        self.assertIn("boom", page)

    def test_unknown_variant_is_refused(self):
        with self.assertRaises(SystemExit):
            h3_ab.main(["run", "--golden", "--variants", "baseline,karras", "--dry-run"])
        with self.assertRaises(SystemExit):
            h3_ab.main(["run", "--golden", "--variants", "beta", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
