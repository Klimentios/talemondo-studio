import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_studio import agent_brain
from local_studio.agent_brain import BrainError, BrainRequest

SCHEMA = {"type": "object", "properties": {"lens_mm": {"type": "integer"}}, "required": ["lens_mm"]}


def fake_cli(folder: Path, name: str, body: str) -> str:
    path = folder / name
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class CommandTests(unittest.TestCase):
    def test_claude_is_read_only_and_structured(self):
        cmd = agent_brain.claude_command("claude", BrainRequest(provider="claude", prompt="hi", schema=SCHEMA))
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Glob,Grep")
        self.assertIn("dontAsk", cmd)
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1]), SCHEMA)
        self.assertNotIn("Bash", " ".join(cmd))

    def test_claude_without_repo_has_no_tools(self):
        cmd = agent_brain.claude_command("claude", BrainRequest(provider="claude", prompt="hi", read_repo=False))
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", cmd)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(json.loads(cmd[cmd.index("--mcp-config") + 1]), {"mcpServers": {}})

    def test_claude_never_falls_back_to_the_settings_model(self):
        cmd = agent_brain.claude_command("claude", BrainRequest(provider="claude", prompt="hi"))
        self.assertEqual(cmd[cmd.index("--model") + 1], agent_brain.CLAUDE_DEFAULT)
        cmd = agent_brain.claude_command("claude", BrainRequest(provider="claude", prompt="hi", model="sonnet"))
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")

    def test_codex_runs_in_read_only_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = agent_brain.codex_command("codex", BrainRequest(provider="codex", prompt="hi", schema=SCHEMA), Path(tmp))
            self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
            self.assertEqual(json.loads((Path(tmp) / "schema.json").read_text()), SCHEMA)
            self.assertEqual(cmd[-1], "hi")

    def test_explicit_effort_reaches_both_clis(self):
        req = BrainRequest(provider="claude", prompt="hi", effort="medium")
        cmd = agent_brain.claude_command("claude", req)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        with tempfile.TemporaryDirectory() as tmp:
            cmd = agent_brain.codex_command("codex", req.model_copy(update={"provider": "codex"}), Path(tmp))
        self.assertIn('model_reasoning_effort="medium"', cmd)
        with self.assertRaises(ValueError):
            BrainRequest(provider="claude", prompt="hi", effort="max")

    def test_clean_env_drops_keys_and_parent_session(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y", "CLAUDECODE": "1",
                                     "ANTHROPIC_BASE_URL": "http://proxy", "HOME": "/Users/me"}):
            env = agent_brain.clean_env()
        self.assertEqual(env["HOME"], "/Users/me")
        self.assertFalse({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDECODE", "ANTHROPIC_BASE_URL"} & set(env))


class ParseTests(unittest.TestCase):
    def test_usage_keeps_unknown_distinct_from_zero(self):
        self.assertIsNone(agent_brain.reported_usage("codex", "ordinary output"))
        self.assertEqual(agent_brain.reported_usage("codex", json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 0}})),
            {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 0})
        self.assertEqual(agent_brain.reported_usage("claude", json.dumps({"usage": {
            "input_tokens": 10, "cache_read_input_tokens": 20, "output_tokens": -1}})),
            {"input_tokens": 10, "cache_read_input_tokens": 20})

    def test_claude_structured_output(self):
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                          "structured_output": {"lens_mm": 85}})
        self.assertEqual(agent_brain.parse_claude(out), ("ok", {"lens_mm": 85}))

    def test_claude_revoked_login_gives_relogin_hint(self):
        out = json.dumps({"subtype": "success", "is_error": True,
                          "result": "Failed to authenticate. API Error: 401 OAuth access token has been revoked."})
        with self.assertRaisesRegex(BrainError, "claude auth login"):
            agent_brain.parse_claude(out)

    def test_codex_schema_answer(self):
        self.assertEqual(agent_brain.parse_codex('{"lens_mm": 50}\n', True), ('{"lens_mm": 50}', {"lens_mm": 50}))
        with self.assertRaises(BrainError):
            agent_brain.parse_codex("fifty", True)


class AskTests(unittest.TestCase):
    def test_codex_round_trip_with_fake_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = fake_cli(Path(tmp), "codex",
                           "import sys,os\nout=sys.argv[sys.argv.index('-o')+1]\n"
                           "assert 'OPENAI_API_KEY' not in os.environ\n"
                           "open(out,'w').write('{\"lens_mm\": 35}')\n")
            with patch.object(agent_brain, "executable", return_value=exe), \
                 patch.dict(os.environ, {"OPENAI_API_KEY": "leak"}):
                result = asyncio.run(agent_brain.ask(BrainRequest(provider="codex", prompt="lens?", schema=SCHEMA)))
        self.assertEqual(result["data"], {"lens_mm": 35})

    def test_timeout_kills_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = fake_cli(Path(tmp), "claude", "import time\ntime.sleep(30)\n")
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(agent_brain._run([exe], Path(tmp), 1))

    def test_missing_cli(self):
        with patch.object(agent_brain, "executable", return_value=None):
            with self.assertRaisesRegex(BrainError, "Codex"):
                asyncio.run(agent_brain.ask(BrainRequest(provider="codex", prompt="hi")))


if __name__ == "__main__":
    unittest.main()
