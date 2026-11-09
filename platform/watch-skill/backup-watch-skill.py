import hashlib
import os
import pathlib
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone

source = pathlib.Path("/data")
backup_dir = pathlib.Path("/backups")
database_names = {"index.db", "lessons.db"}
excluded_names = {"cache", "bin"}
if not (source / "index.db").is_file() or (source / "index.db").stat().st_size == 0:
    raise SystemExit("Watch Skill index is missing or empty; refusing an empty backup.")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_dir.mkdir(parents=True, exist_ok=True)
os.chmod(backup_dir, 0o700)
final_path = backup_dir / f"watch-skill-{stamp}.tar.gz"

with tempfile.TemporaryDirectory(prefix="watch-skill-backup-", dir="/tmp") as tmp_name:
    snapshot = pathlib.Path(tmp_name) / "watch-skill"
    snapshot.mkdir(mode=0o700)
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = pathlib.Path(current)
        relative = current_path.relative_to(source)
        directories[:] = [
            name for name in directories
            if name not in excluded_names and not name.startswith(".env")
            and not (current_path / name).is_symlink()
        ]
        destination_dir = snapshot / relative
        destination_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            item = current_path / name
            if item.is_symlink() or name in database_names or name.endswith(("-wal", "-shm")) or name == ".env" or name.startswith(".env."):
                continue
            destination = destination_dir / name
            shutil.copy2(item, destination)

    for name in database_names:
        current_path = source / name
        if not current_path.is_file() or current_path.stat().st_size == 0:
            continue
        target_path = snapshot / name
        with sqlite3.connect(f"file:{current_path}?mode=ro", uri=True, timeout=30) as current:
            with sqlite3.connect(target_path) as target:
                current.backup(target, pages=256, sleep=0.1)
        os.chmod(target_path, 0o600)

    temporary_archive = backup_dir / f".{final_path.name}.part"
    with tarfile.open(temporary_archive, "w:gz", compresslevel=6) as archive:
        archive.add(snapshot, arcname="watch-skill")
    os.chmod(temporary_archive, 0o600)
    with tarfile.open(temporary_archive, "r:gz") as archive:
        members = archive.getmembers()
        if not members or any(member.issym() or member.islnk() or ".." in pathlib.PurePosixPath(member.name).parts for member in members):
            raise SystemExit("Watch Skill backup archive failed path validation.")
    os.replace(temporary_archive, final_path)

digest = hashlib.sha256()
with final_path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
checksum = final_path.with_suffix(final_path.suffix + ".sha256")
checksum.write_text(f"{digest.hexdigest()}  {final_path.name}\n")
os.chmod(checksum, 0o600)

archives = sorted(backup_dir.glob("watch-skill-*.tar.gz"), key=lambda item: item.stat().st_mtime, reverse=True)
cutoff = datetime.now(timezone.utc) - timedelta(days=14)
for index, old in enumerate(archives):
    age = datetime.fromtimestamp(old.stat().st_mtime, timezone.utc)
    if old != final_path and (index >= 2 or age < cutoff):
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)

print(f"Created Watch Skill backup {final_path.name}: {final_path.stat().st_size} bytes, sha256={digest.hexdigest()}", flush=True)
