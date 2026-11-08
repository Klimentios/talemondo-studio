import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from local_studio.compute.keychain import KeychainError, KeychainStore


class KeychainStoreTests(unittest.TestCase):
    def setUp(self):
        self.keychain = KeychainStore(swift_path="/usr/bin/swift", helper_path=Path("/tmp/keychain.swift"))

    @patch("local_studio.compute.keychain.subprocess.run")
    def test_set_sends_secret_only_over_stdin(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        self.keychain.set("vast", "vast-secret-token")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/swift", "/tmp/keychain.swift", "set", "vast"])
        self.assertEqual(kwargs["input"], b"vast-secret-token")
        self.assertNotIn("vast-secret-token", repr(args[0]))
        self.assertNotIn("vast-secret-token", repr(kwargs.get("env")))

    @patch("local_studio.compute.keychain.subprocess.run")
    def test_get_returns_stdout_only_to_caller_memory(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"secret-value", stderr=b"")
        self.assertEqual(self.keychain.get("vast"), "secret-value")
        self.assertEqual(run.call_args.args[0][-2:], ["get", "vast"])
        self.assertNotIn("secret-value", str(run.call_args.args[0]))

    @patch("local_studio.compute.keychain.subprocess.run")
    def test_missing_item_returns_none(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=2, stdout=b"", stderr=b"")
        self.assertIsNone(self.keychain.get("vast"))

    @patch("local_studio.compute.keychain.subprocess.run")
    def test_delete_is_idempotent(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        self.keychain.delete("vast")
        self.assertEqual(run.call_args.args[0][-2:], ["delete", "vast"])

    @patch("local_studio.compute.keychain.subprocess.run")
    def test_errors_are_sanitized_and_do_not_reveal_input_or_stderr(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"token leaked")
        with self.assertRaises(KeychainError) as caught:
            self.keychain.set("vast", "token leaked")
        self.assertNotIn("token leaked", str(caught.exception))

    def test_provider_and_secret_are_validated(self):
        with self.assertRaises(ValueError):
            self.keychain.get("other-provider")
        with self.assertRaises(ValueError):
            self.keychain.set("vast", "  ")
        with self.assertRaises(ValueError):
            self.keychain.set("vast", "x" * 8193)

    def test_swift_helper_uses_security_framework_and_never_reads_secret_from_args(self):
        source = Path("local_studio/compute/keychain.swift").read_text()
        self.assertIn("import Security", source)
        self.assertIn("FileHandle.standardInput.readDataToEndOfFile()", source)
        self.assertNotIn("CommandLine.arguments[3]", source)
        self.assertNotIn("ProcessInfo.processInfo.environment", source)


if __name__ == "__main__":
    unittest.main()
