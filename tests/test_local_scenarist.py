import asyncio
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from local_studio import agent_tasks
from local_studio.agent_brain import BrainError
from local_studio.scenarist import SceneBrief, make_router
from local_studio.store import StudioStore


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class LocalScenaristTests(unittest.TestCase):
    """«✍ Сценарист» is written by the Studio agent (Claude/Codex by subscription), not by a Qwen on the GPU."""

    def test_agent_sees_the_hero_picture_and_even_shot_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            png = store.assets_dir / "hero.png"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=1200x1600",
                            "-frames:v", "1", str(png)], check=True)
            data = png.read_bytes()
            asset = store.add_asset(kind="image", role="Лицо героя", original_name="hero.png", mime="image/png",
                                    size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=png)
            asked = []

            async def fake_ask(prompt, images):
                asked.append((prompt, [Path(path).read_bytes()[:2] for path in images]))
                return "not the six sections"

            route = next(r for r in make_router(store, "").routes if r.path == "/api/scenarist")
            brief = SceneBrief(brief="Вега танцует", duration=10, shots=2,
                               references=[{"asset_id": asset["asset_id"], "kind": "image", "number": 1,
                                            "role": "Лицо героя", "owner": "VEGA"}])
            with patch.object(agent_tasks, "_ask", fake_ask):
                result = asyncio.run(route.endpoint(brief))
            prompt, pictures = asked[0]
            self.assertEqual(pictures, [b"\xff\xd8"])  # the hero preview reaches the agent as a JPEG file
            self.assertIn("<Picture 1>", prompt)
            self.assertIn("[Shot 2]", prompt)
            self.assertEqual(result["shot_headers"][1].split()[0:2], ["[Shot", "2]"])
            self.assertEqual(len(asked), 2, "a wrong format gets one correction round, like before")
            self.assertTrue(result["problems"])  # still wrong after the round: the page shows the remarks

    def test_agent_failure_is_a_readable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            route = next(r for r in make_router(StudioStore(Path(temp)), "").routes if r.path == "/api/scenarist")

            async def down(prompt, images):
                raise BrainError("Claude Code не вошёл в подписку")

            with patch.object(agent_tasks, "_ask", down):
                with self.assertRaises(HTTPException) as caught:
                    asyncio.run(route.endpoint(SceneBrief(brief="Вега танцует")))
            self.assertEqual(caught.exception.status_code, 502)
            self.assertIn("Агент не написал промпт", caught.exception.detail)
            self.assertIn("подписку", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
