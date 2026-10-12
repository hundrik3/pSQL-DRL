"""Regression checks for destructive-operation gates and recovery failure reporting."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("controller", ROOT / "scripts/lab.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

def bare_lab():
    lab = controller.Lab.__new__(controller.Lab)
    lab.state = {"id": "11111111-1111-4111-8111-111111111111",
                 "project": "psqldrl_11111111111141118111111111111111"}
    lab.project = lab.state["project"]
    return lab

def result(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)

class ControllerRegressionTests(unittest.TestCase):
    def test_optional_missing_volume_is_absent(self):
        lab = bare_lab()
        with patch.object(controller, "run", return_value=result(1, "[]",
                          "Error response from daemon: get volume: no such volume")):
            self.assertIsNone(lab.volume("primary", required=False))

    def test_docker_failure_is_not_volume_absence(self):
        lab = bare_lab()
        with patch.object(controller, "run", return_value=result(1, "", "Cannot connect to Docker")):
            with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                lab.volume("primary", required=False)

    def test_recovery_cannot_mount_source_volume_even_with_valid_labels(self):
        lab = bare_lab()
        lab.compose = Mock(return_value=result(stdout="owned-container\n"))
        lab.volume = Mock()
        labels = {"io.psqldrl.managed": "true", "io.psqldrl.instance": lab.state["id"],
                  "com.docker.compose.project": lab.project,
                  "com.docker.compose.service": "recovery"}
        info = {"Config": {"Labels": labels},
                "Mounts": [{"Type": "volume", "Name": lab.project + "_primary",
                            "Destination": "/var/lib/postgresql/data"}]}
        with patch.object(controller, "run", return_value=result(stdout=json.dumps([info]))):
            with self.assertRaisesRegex(RuntimeError, "role or mount destination"):
                lab.container("recovery")

    def test_same_second_legacy_backup_suffix_does_not_reorder_fractional_ids(self):
        # A raw lexical sort places the legacy Z after every microsecond digit.
        old = "20261012T120000Z-ffffffff"
        first = "20261012T120000000001Z-ffffffff"
        second = "20261012T120000000002Z-00000000"
        self.assertEqual(sorted([second, old, first], key=controller.backup_sort_key),
                         [old, first, second])

    def test_new_backup_time_has_microsecond_resolution(self):
        value = "20261012T120000123456Z-abcd1234"
        self.assertEqual(controller.backup_sort_key(value)[0],
                         dt.datetime(2026, 10, 12, 12, 0, 0, 123456))

    def test_docker_error_cannot_count_as_detected_backup_corruption(self):
        for mode in ("corrupt-data", "missing-data", "missing-wal"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(RuntimeError, "was not observed"):
                    controller.require_verifier_rejection(mode,
                         result(1, "", "Cannot connect to the Docker daemon"))

    def test_expected_native_verifier_errors_are_recognized(self):
        diagnostics = {
            "corrupt-data": "pg_verifybackup: error: global/pg_control: CRC is incorrect",
            "missing-data": 'pg_verifybackup: error: "global/pg_control" is present in the manifest but not on disk',
            "missing-wal": "pg_verifybackup: error: WAL parsing failed for timeline 1",
        }
        for mode, diagnostic in diagnostics.items():
            with self.subTest(mode=mode):
                controller.require_verifier_rejection(mode, result(1, "", diagnostic))

    def test_empty_pitr_target_fails_before_creating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            process = subprocess.run([sys.executable, str(ROOT / "scripts/lab.py"),
                                      "--state", str(state), "restore", "--target", ""],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertIn("PITR target must not be empty", process.stderr)
            self.assertFalse(state.exists())

    def test_conflicting_restore_targets_fail_before_creating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            process = subprocess.run([sys.executable, str(ROOT / "scripts/lab.py"),
                                     "--state", str(state), "restore", "--target",
                                     "2026-10-12T12:00:00+00:00", "--base-only"],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertIn("cannot be combined", process.stderr)
            self.assertFalse(state.exists())

    def test_cleanup_error_is_preserved_in_failed_report(self):
        lab = bare_lab()
        with tempfile.TemporaryDirectory() as directory:
            lab.log_dir = Path(directory)
            lab.up = Mock(side_effect=RuntimeError("Startup failed"))
            lab.clean = Mock(side_effect=RuntimeError("Cleanup failed"))
            with self.assertRaisesRegex(RuntimeError, "Cleanup failed"):
                lab.test()
            report = json.loads((lab.log_dir / "report.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"], "Startup failed")
            self.assertEqual(report["cleanup_error"], "Cleanup failed")
            self.assertEqual(report["checks"][-1]["status"], "failed")

if __name__ == "__main__":
    unittest.main()
