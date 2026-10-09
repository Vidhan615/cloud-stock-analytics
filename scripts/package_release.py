"""Package an allowlist of application files; never include local data or secrets."""

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [
        ROOT / "wsgi.py",
        ROOT / "requirements.txt",
        ROOT / "requirements.lock",
        ROOT / "LICENSE",
    ]
    for folder in ["cloudfolio", "data"]:
        files.extend(
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
        )
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"Application release: {output}")
    print(f"SHA256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    package(ROOT / "build" / "cloudfolio.zip")
