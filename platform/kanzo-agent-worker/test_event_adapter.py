"""Tests for Kanzo SSE wake and reconnect reconciliation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import event_adapter


class FakeResponse:
    status = 200

    def __init__(self, lines: list[bytes]):
        self.lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def readline(self) -> bytes:
        return next(self.lines, b"")


class EventAdapterTests(unittest.TestCase):
    def test_reconnect_wakes_for_reconciliation_and_change_events_wake_worker(self) -> None:
        first = FakeResponse([b": keepalive\n"])
        second = FakeResponse([
            b"event: unrelated\n",
            b"data: ignored\n",
            b"\n",
            b"event: kanzo.changed\n",
            b"data: {}\n",
            b"\n",
        ])

        with patch.object(event_adapter.urllib.request, "urlopen", side_effect=[first, second]) as open_stream:
            with patch.object(event_adapter, "wake_worker") as wake:
                with self.assertRaises(ConnectionError):
                    event_adapter.listen_once()
                with self.assertRaises(ConnectionError):
                    event_adapter.listen_once()

        self.assertEqual(open_stream.call_count, 2)
        # Each connection reconciles current state, including after reconnect;
        # only the Kanzo change event adds a wake within the stream.
        self.assertEqual(wake.call_count, 3)


if __name__ == "__main__":
    unittest.main()
