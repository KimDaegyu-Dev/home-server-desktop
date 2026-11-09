import hashlib
import os
import pathlib
import shutil
import sqlite3
import sys
import tarfile
import tempfile

archive_path = pathlib.Path(sys.argv[1])
expected_digest = sys.argv[2].lower()
target = pathlib.Path("/data")
digest = hashlib.sha256()
with archive_path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
actual = digest.hexdigest()
if actual != expected_digest:
    raise SystemExit("Watch Skill archive checksum mismatch.")
if (target / "index.db").exists() and (target / "index.db").stat().st_size > 0:
    raise SystemExit("Watch Skill PVC already contains an index; refusing to overwrite it.")

with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    seen = set()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        legacy_root_file = len(path.parts) == 1 and path.name in {"index.db", "lessons.db"} and member.isfile()
        app_rooted = path.parts[:1] == ("watch-skill",)
        if path.is_absolute() or ".." in path.parts or member.name in seen or not (member.isfile() or member.isdir()) or not (app_rooted or legacy_root_file):
            raise SystemExit("Watch Skill archive contains an unexpected or unsafe entry.")
        seen.add(member.name)
        if member.size > 5 * 1024 * 1024 * 1024:
            raise SystemExit("Watch Skill archive contains an oversized file.")
    if not any((item.name == "watch-skill/index.db" or item.name == "index.db") and item.isfile() for item in members):
        raise SystemExit("Watch Skill archive has no index.db.")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="watch-skill-restore-", dir="/tmp") as temp_name:
        staged = pathlib.Path(temp_name)
        for member in members:
            member_path = pathlib.PurePosixPath(member.name)
            relative = member_path.relative_to("watch-skill") if member_path.parts[:1] == ("watch-skill",) else member_path
            if not relative.parts:
                continue
            destination = staged.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit(f"Archive member {member.name} could not be read.")
            with stream, destination.open("wb") as output:
                shutil.copyfileobj(stream, output)
            os.chmod(destination, 0o600 if destination.suffix == ".db" else 0o640)

        for name in ("index.db", "lessons.db"):
            database = staged / name
            if database.is_file():
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise SystemExit(f"Restored Watch Skill {name} SQLite integrity check failed.")

        for entry in staged.iterdir():
            destination = target / entry.name
            if destination.exists():
                raise SystemExit(f"Watch Skill PVC already contains {entry.name}; refusing to overwrite it.")
        for entry in staged.iterdir():
            os.replace(entry, target / entry.name)

print(f"Restored and verified Watch Skill index ({(target / 'index.db').stat().st_size} bytes).", flush=True)
