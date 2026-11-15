"""Tests for bounded worker phase telemetry."""

from __future__ import annotations

import unittest

from worker import Worker


class WorkerPhaseMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = Worker.__new__(Worker)
        self.worker.metrics = {"phase_duration": {"count": {}, "sum": {}, "buckets": {}}}

    def test_records_only_completed_run_phases(self) -> None:
        self.worker._record_phase_durations(
            [
                {"state": "claimed", "at": "2026-11-15T00:00:00Z"},
                {"state": "preparing", "at": "2026-11-15T00:00:10Z"},
                {"state": "running", "at": "2026-11-15T00:00:40Z"},
                {"state": "finalizing", "at": "2026-11-15T00:01:40Z"},
                {"state": "succeeded", "at": "2026-11-15T00:01:50Z"},
            ],
            "done",
        )

        metric = self.worker.metrics["phase_duration"]
        self.assertEqual(metric["count"], {
            "claimed|done": 1, "preparing|done": 1,
            "running|done": 1, "finalizing|done": 1,
        })
        self.assertEqual(metric["sum"]["running|done"], 60)
        self.assertEqual(metric["buckets"]["running|done"][60], 1)

    def test_ignores_incomplete_malformed_and_negative_intervals(self) -> None:
        self.worker._record_phase_durations(
            [
                {"state": "claimed", "at": "bad-time"},
                {"state": "running", "at": "2026-11-15T00:01:00Z"},
                {"state": "review", "at": "2026-11-15T00:00:00Z"},
                {"state": "finalizing", "at": "2026-11-15T00:02:00Z"},
            ],
            "review",
        )

        self.assertEqual(self.worker.metrics["phase_duration"]["count"], {})


if __name__ == "__main__":
    unittest.main()
