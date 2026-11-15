import signal
import unittest
from unittest.mock import patch

import event_adapter


class EventAdapterSignalTests(unittest.TestCase):
    def test_pid1_handles_sigterm_and_sigint(self):
        registered = []

        with patch.object(event_adapter.signal, "signal", side_effect=lambda sig, handler: registered.append((sig, handler))):
            event_adapter.install_signal_handlers()

        self.assertEqual([sig for sig, _ in registered], [signal.SIGTERM, signal.SIGINT])
        for sig, handler in registered:
            with self.subTest(signal=sig), self.assertRaises(SystemExit) as raised:
                handler(sig, None)
            self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
