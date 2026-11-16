"""Tests that ReadOnly profile sync delivers and preserves Hermes memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from readonly_executor import _sync_profile_state


class ReadOnlyProfileMemorySyncTests(unittest.TestCase):
    def test_nested_hermes_memory_is_synced_and_local_learning_survives_resync(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source" / "profiles" / "researcher"
            destination = root / "readonly" / "profiles" / "researcher"
            (source / "memories").mkdir(parents=True)
            destination.mkdir(parents=True)
            source_memory = source / "memories" / "MEMORY.md"
            source_user = source / "memories" / "USER.md"
            source_memory.write_text("Shared verified notes\n", encoding="utf-8")
            source_user.write_text("User preferences\n", encoding="utf-8")

            _sync_profile_state(source, destination)

            local_memory = destination / "memories" / "MEMORY.md"
            local_user = destination / "memories" / "USER.md"
            self.assertEqual(local_memory.read_text(encoding="utf-8"), "Shared verified notes\n")
            self.assertEqual(local_user.read_text(encoding="utf-8"), "User preferences\n")

            # A replacement Pod uses the same PVC but starts a new executor process.
            # Re-running profile sync must preserve local ReadOnly learning.
            local_memory.write_text(
                "Shared verified notes\nReadOnly task learned this\n", encoding="utf-8"
            )
            _sync_profile_state(source, destination)
            self.assertEqual(
                local_memory.read_text(encoding="utf-8"),
                "Shared verified notes\nReadOnly task learned this\n",
            )

    def test_updated_source_memory_flows_when_destination_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            destination = root / "readonly"
            (source / "memories").mkdir(parents=True)
            destination.mkdir()
            source_memory = source / "memories" / "MEMORY.md"
            source_memory.write_text("First version\n", encoding="utf-8")

            _sync_profile_state(source, destination)
            source_memory.write_text("Updated source memory\n", encoding="utf-8")
            _sync_profile_state(source, destination)

            self.assertEqual(
                (destination / "memories" / "MEMORY.md").read_text(encoding="utf-8"),
                "Updated source memory\n",
            )


if __name__ == "__main__":
    unittest.main()
