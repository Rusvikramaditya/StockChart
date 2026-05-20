"""Phase 6-11 Windows scheduler script tests."""

from __future__ import annotations

import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"


class SchedulerPhase6Test(unittest.TestCase):
    def test_daily_scan_runner_defaults_to_nifty500_and_logs_output(self):
        path = SCRIPTS_DIR / "run_daily_scan.ps1"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")

        self.assertIn('[string]$Universe = "nifty500"', content)
        self.assertIn("--universe", content)
        self.assertIn("--workers", content)
        self.assertIn("--output", content)
        self.assertIn("output", content)
        self.assertIn("logs", content)
        self.assertIn("Tee-Object", content)

    def test_task_installer_uses_daily_1545_schedule_and_runner(self):
        path = SCRIPTS_DIR / "install_daily_scan_task.ps1"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")

        self.assertIn("SupportsShouldProcess", content)
        self.assertIn('[string]$TaskName = "PatternFinderDailyScan"', content)
        self.assertIn('[string]$Universe = "nifty500"', content)
        self.assertIn('[string]$StartTime = "15:45"', content)
        self.assertIn("run_daily_scan.ps1", content)
        self.assertIn("Quote-TaskArg", content)
        self.assertIn("New-ScheduledTaskAction", content)
        self.assertIn("New-ScheduledTaskTrigger -Daily", content)
        self.assertIn("Register-ScheduledTask", content)


if __name__ == "__main__":
    unittest.main()
