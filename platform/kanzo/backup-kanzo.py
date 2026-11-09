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
database = source / "kanzo.db"
if not database.is_file() or database.stat().st_size == 0:
    raise SystemExit("Kanzo database is missing or empty; refusing to create an empty backup.")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
archive_path = backup_dir / f"kanzo-{stamp}.tar.gz"
backup_dir.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="kanzo-backup-", dir="/tmp") as temp_name:
    snapshot = pathlib.Path(temp_name) / "kanzo"
    snapshot.mkdir(mode=0o700)
    for item in source.iterdir():
        if item.name in {"kanzo.db", "kanzo.db-wal", "kanzo.db-shm"}:
            continue
        destination = snapshot / item.name
        if item.is_dir():
            shutil.copytree(item, destination, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, destination)

    target_db = snapshot / "kanzo.db"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30) as current:
        with sqlite3.connect(target_db) as consistent:
            current.backup(consistent, pages=256, sleep=0.1)
    os.chmod(target_db, 0o600)

    with tarfile.open(archive_path, "w:gz", compresslevel=6) as archive:
        archive.add(snapshot, arcname="kanzo")

os.chmod(archive_path, 0o600)
digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
checksum_path.write_text(f"{digest}  {archive_path.name}\n")
os.chmod(checksum_path, 0o600)

cutoff = datetime.now(timezone.utc) - timedelta(days=14)
for old in backup_dir.glob("kanzo-*.tar.gz"):
    if old != archive_path and datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)

with tarfile.open(archive_path, "r:gz") as archive:
    archive.getmembers()
print(f"Created SQLite-consistent backup {archive_path.name}: {archive_path.stat().st_size} bytes, sha256={digest}", flush=True)
