"""Phase 1 contract checks, runnable without third-party dependencies."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("preflight", ROOT / "scripts/preflight.py")
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class FoundationTests(unittest.TestCase):
    def test_no_credentials_in_environment_template(self):
        values = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                      if line and not line.startswith("#"))
        for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "ADMIN_API_TOKEN",
                    "GRAFANA_ADMIN_PASSWORD", "INGEST_TOKEN", "INFERENCE_API_TOKEN", "ALERT_API_TOKEN"):
            self.assertEqual(values[key], "", key)

    def test_missing_executable_is_a_failed_check(self):
        with patch.object(preflight.shutil, "which", return_value=None):
            result = preflight.probe("missing", ["missing"])
        self.assertFalse(result["ok"])

    def test_timeout_is_reported_without_crashing(self):
        with patch.object(preflight.shutil, "which", return_value="/bin/tool"):
            with patch.object(preflight.subprocess, "run",
                              side_effect=preflight.subprocess.TimeoutExpired("tool", 15)):
                result = preflight.probe("tool", ["tool"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail"], "TimeoutExpired")


if __name__ == "__main__":
    unittest.main()
