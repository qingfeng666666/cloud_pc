"""Read-only checks for the original WireGuard AAR and source archives."""
import argparse
import base64
import hashlib
import tarfile
import zipfile
from pathlib import Path


def verify(source_dir, aar_path, apk_path):
    source_dir = Path(source_dir)
    with tarfile.open(source_dir / "wireguard-android-1.0.20260102.tar.gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("/tunnel/tools/libwg-go/go.sum"))
        sums = tar.extractfile(member).read().decode()
    for path in sorted(source_dir.glob("*.zip")):
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None, f"Corrupt ZIP: {path}"
            digest = hashlib.sha256()
            for name in sorted(n for n in archive.namelist() if not n.endswith("/")):
                digest.update((hashlib.sha256(archive.read(name)).hexdigest() + "  " + name + "\n").encode())
            value = "h1:" + base64.b64encode(digest.digest()).decode()
            if path.name not in ("golang-x-term.zip", "golang-x-text.zip"):
                assert value in sums, f"Not in upstream go.sum: {path}"
            print(path.name, value)
    with zipfile.ZipFile(aar_path) as aar, zipfile.ZipFile(apk_path) as apk:
        libraries = [n for n in aar.namelist() if n.startswith("jni/") and n.endswith(".so")]
        assert len(libraries) == 12, "Unexpected original AAR library layout"
        for name in libraries:
            assert aar.read(name) == apk.read(name.replace("jni/", "lib/", 1)), name
        assert "assets/licenses/CLOUD-PC-MIT.txt" in apk.namelist()
        assert "assets/licenses/THIRD_PARTY_NOTICES.txt" in apk.namelist()
        print("Unmodified WireGuard native libraries:", len(libraries))
    print("Checks passed; this does not constitute a legal certification.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir")
    parser.add_argument("aar")
    parser.add_argument("apk")
    args = parser.parse_args()
    verify(args.source_dir, args.aar, args.apk)
