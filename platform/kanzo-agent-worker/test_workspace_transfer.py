from __future__ import annotations

import gzip
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from workspace_transfer import (
    WorkspaceTransferError,
    apply_changed_archive,
    build_manifest_archive,
    build_source_archive,
)


def init_repo(path: Path, name: str, content: str) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--initial-branch=main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Workspace Transfer Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "transfer-test@example.invalid"], check=True)
    (path / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", name], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "base"], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def output_archive(files: dict[str, str], deleted: list[str] | None = None) -> bytes:
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            manifest = json.dumps({"schema": "kanzo-output/v1", "deleted": deleted or []}).encode()
            info = tarfile.TarInfo("kanzo-output.json")
            info.size = len(manifest)
            archive.addfile(info, io.BytesIO(manifest))
            for path, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class ManifestTransferTests(unittest.TestCase):
    def test_manifest_archive_namespaces_pinned_secondary_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "primary"
            secondary = root / "secondary"
            primary_sha = init_repo(primary, "README.md", "primary\n")
            secondary_sha = init_repo(secondary, "api.md", "api contract\n")

            archive_bytes, digest = build_manifest_archive(
                primary, primary_sha, [("api", secondary, secondary_sha)],
            )

            self.assertEqual(len(digest), 64)
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
                names = set(archive.getnames())
            self.assertEqual(names, {"README.md", ".kanzo-sources/api/api.md"})
            self.assertFalse(any(".git" in name.split("/") for name in names))

    def test_manifest_archive_rejects_duplicate_source_names_and_path_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "primary"
            secondary = root / "secondary"
            primary.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main", str(primary)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(primary), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(primary), "config", "user.email", "test@example.invalid"], check=True)
            (primary / "README.md").write_text("primary\n", encoding="utf-8")
            (primary / ".kanzo-sources").mkdir()
            (primary / ".kanzo-sources" / "api").mkdir()
            (primary / ".kanzo-sources" / "api" / "api.md").write_text("collision\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(primary), "add", "README.md", ".kanzo-sources"], check=True)
            subprocess.run(["git", "-C", str(primary), "commit", "-m", "base"], check=True, capture_output=True)
            primary_sha = subprocess.run(["git", "-C", str(primary), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            secondary_sha = init_repo(secondary, "api.md", "api\n")
            with self.assertRaises(WorkspaceTransferError):
                build_manifest_archive(primary, primary_sha, [("api", secondary, secondary_sha)])

            with self.assertRaises(WorkspaceTransferError):
                build_manifest_archive(primary, primary_sha, [("api", secondary, secondary_sha), ("API", secondary, secondary_sha)])

    def test_manifest_archive_applies_file_and_expanded_limits_across_all_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "primary"
            secondary = root / "secondary"
            primary_sha = init_repo(primary, "README.md", "primary\n")
            secondary_sha = init_repo(secondary, "api.md", "api contract\n")

            with self.assertRaisesRegex(WorkspaceTransferError, "max_files"):
                build_manifest_archive(
                    primary, primary_sha, [("api", secondary, secondary_sha)], max_files=1,
                )
            with self.assertRaisesRegex(WorkspaceTransferError, "max_expanded_bytes"):
                build_manifest_archive(
                    primary, primary_sha, [("api", secondary, secondary_sha)], max_expanded_bytes=20,
                )

    def test_manifest_archive_applies_compressed_limit_across_all_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "primary"
            secondary = root / "secondary"
            primary_sha = init_repo(primary, "README.md", "primary\n")
            secondary_sha = init_repo(secondary, "api.md", "api contract\n")
            primary_archive, _ = build_source_archive(primary, primary_sha)
            secondary_archive, _ = build_source_archive(secondary, secondary_sha)
            combined_limit = max(len(primary_archive), len(secondary_archive)) + 1

            with self.assertRaisesRegex(WorkspaceTransferError, "max_compressed_bytes"):
                build_manifest_archive(
                    primary, primary_sha, [("api", secondary, secondary_sha)],
                    max_compressed_bytes=combined_limit,
                )

    def test_secondary_source_edits_fail_closed_before_primary_changes_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "README.md").write_text("before\n", encoding="utf-8")
            changed = output_archive({
                "README.md": "primary change\n",
                ".kanzo-sources/api/api.md": "attempted secondary edit\n",
            })
            with self.assertRaisesRegex(WorkspaceTransferError, "read-only source path"):
                apply_changed_archive(checkout, changed, protected_prefixes=(".kanzo-sources",))
            self.assertEqual((checkout / "README.md").read_text(encoding="utf-8"), "before\n")
            self.assertFalse((checkout / ".kanzo-sources").exists())

    def test_secondary_source_deletions_are_also_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / ".kanzo-sources" / "api").mkdir(parents=True)
            (checkout / ".kanzo-sources" / "api" / "api.md").write_text("contract\n", encoding="utf-8")
            changed = output_archive({}, deleted=[".kanzo-sources/api/api.md"])
            with self.assertRaisesRegex(WorkspaceTransferError, "read-only source path"):
                apply_changed_archive(checkout, changed, protected_prefixes=(".kanzo-sources",))
            self.assertEqual((checkout / ".kanzo-sources" / "api" / "api.md").read_text(encoding="utf-8"), "contract\n")


if __name__ == "__main__":
    unittest.main()
