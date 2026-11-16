import unittest
from unittest.mock import Mock

from reconcile_hermes_pm import VENV_DRIFT, reconcile_environment


class ReconcileHermesPmTests(unittest.TestCase):
    def test_current_environment_is_not_reinstalled(self):
        activate = Mock(return_value=[])
        sync = Mock()

        self.assertFalse(reconcile_environment(activate, sync))
        activate.assert_called_once_with()
        sync.assert_not_called()

    def test_known_drift_is_synced_and_verified(self):
        activate = Mock(side_effect=[[VENV_DRIFT], []])
        sync = Mock()

        self.assertTrue(reconcile_environment(activate, sync))
        sync.assert_called_once_with(explicit=True)
        self.assertEqual(activate.call_count, 2)

    def test_unrelated_preflight_error_fails_without_sync(self):
        activate = Mock(return_value=["pnpm: missing"])
        sync = Mock()

        with self.assertRaisesRegex(RuntimeError, "pnpm: missing"):
            reconcile_environment(activate, sync)
        sync.assert_not_called()

    def test_persistent_drift_after_sync_fails_closed(self):
        activate = Mock(side_effect=[[VENV_DRIFT], [VENV_DRIFT]])
        sync = Mock()

        with self.assertRaisesRegex(RuntimeError, "remains unhealthy"):
            reconcile_environment(activate, sync)
        sync.assert_called_once_with(explicit=True)


if __name__ == "__main__":
    unittest.main()
