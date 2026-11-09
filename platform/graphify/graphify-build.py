import json
import os
import pathlib
import subprocess
import sys
import tarfile
import urllib.request

revision = os.environ["GRAPHIFY_SOURCE_REVISION"]
repo = "KimDaegyu-Dev/home-server-desktop"
archive = f"https://codeload.github.com/{repo}/tar.gz/{revision}"
source = pathlib.Path("/work/source")
output = pathlib.Path("/work/output")
source.mkdir(parents=True, exist_ok=True)
output.mkdir(parents=True, exist_ok=True)
allowed = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".sql"}

request = urllib.request.Request(archive, headers={"User-Agent": "home-server-graphify-indexer"})
with urllib.request.urlopen(request, timeout=90) as response, tarfile.open(fileobj=response, mode="r|gz") as bundle:
    for member in bundle:
        parts = pathlib.PurePosixPath(member.name).parts
        if len(parts) < 2 or not member.isfile() or pathlib.Path(parts[-1]).suffix.lower() not in allowed:
            continue
        relative = pathlib.Path(*parts[1:])
        destination = (source / relative).resolve()
        if source.resolve() not in destination.parents:
            raise RuntimeError(f"archive path escapes source: {member.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stream = bundle.extractfile(member)
        if stream is None:
            continue
        with stream, destination.open("wb") as target:
            target.write(stream.read())

count = sum(1 for path in source.rglob("*") if path.is_file())
if count == 0:
    raise SystemExit("Pinned source archive contained no eligible code files.")

env = dict(os.environ, PYTHONPATH="/opt/graphify/site")
subprocess.run(
    [sys.executable, "-m", "graphify", "extract", str(source), "--out", str(output), "--max-workers", "2", "--max-concurrency", "1"],
    check=True,
    env=env,
)
graph = output / "graphify-out" / "graph.json"
if not graph.is_file() or graph.stat().st_size == 0:
    raise SystemExit("Graphify did not produce a non-empty graph.json.")

target = pathlib.Path("/graph/graph.json")
target.parent.mkdir(parents=True, exist_ok=True)
staging = target.with_suffix(".json.tmp")
staging.write_bytes(graph.read_bytes())
os.replace(staging, target)
(target.parent / "source.json").write_text(json.dumps({"repository": repo, "revision": revision, "code_files": count}, sort_keys=True) + "\n")
print(f"Indexed {count} code files at {revision}; graph size {target.stat().st_size} bytes.", flush=True)
