"""Cover shared SearXNG endpoint propagation into persistent Hermes profiles."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_hermes_env


class SyncHermesEnvTests(unittest.TestCase):
    def test_searxng_url_is_upserted_for_default_and_worker_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            homes = [root, *(root / "profiles" / name for name in ("researcher", "builder", "reviewer"))]
            for home in homes:
                home.mkdir(parents=True, exist_ok=True)
                (home / ".env").write_text(
                    "KEEP_ME=unchanged\nSEARXNG_URL=http://old.invalid\n",
                    encoding="utf-8",
                )

            with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.search.svc.cluster.local:8080"}):
                module = importlib.reload(sync_hermes_env)
                with patch.object(module, "ROOT", root):
                    module.main()

            for home in homes:
                content = (home / ".env").read_text(encoding="utf-8")
                self.assertEqual(content.count("SEARXNG_URL="), 1)
                self.assertIn("SEARXNG_URL=http://searxng.search.svc.cluster.local:8080", content)
                self.assertIn("KEEP_ME=unchanged", content)
                if os.name == "posix":
                    self.assertEqual((home / ".env").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
