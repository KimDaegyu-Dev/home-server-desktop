import hashlib
import io
import os
import pathlib
import tarfile
import urllib.request

url = "https://github.com/emanuele-em/kanzo/releases/download/v0.4.1/kanzo-x86_64-unknown-linux-musl.tar.xz"
expected = "660622f9b366d5a459b5b850b177370f76e18d9819993d0f0fabce00ae076c41"
target = pathlib.Path("/opt/kanzo/kanzo")
target.parent.mkdir(parents=True, exist_ok=True)
request = urllib.request.Request(url, headers={"User-Agent": "home-server-kanzo-installer"})
with urllib.request.urlopen(request, timeout=90) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != expected:
    raise SystemExit("Kanzo release checksum mismatch.")

member_name = "kanzo-x86_64-unknown-linux-musl/kanzo"
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as bundle:
    member = bundle.getmember(member_name)
    if not member.isfile():
        raise SystemExit("Kanzo archive does not contain the expected regular binary.")
    stream = bundle.extractfile(member)
    if stream is None:
        raise SystemExit("Kanzo binary could not be extracted.")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(stream.read())
    os.chmod(temporary, 0o555)
    os.replace(temporary, target)
print("Installed checksum-verified Kanzo 0.4.1 binary.", flush=True)
