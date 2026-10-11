"""Cloud-PC host entry point. Does not load digital_human services or models."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared_components"))
sys.path.insert(0, str(ROOT / "desktop_version"))

from host.main import main

if __name__ == "__main__":
    raise SystemExit(main())
