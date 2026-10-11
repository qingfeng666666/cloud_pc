"""Frozen entry point for Cloud Slave.

``--root`` must be installed before importing *any* slave module.  Frozen
executables may load a package dependency while resolving the entry module;
if ``slave.config`` sees the environment before that value exists, it freezes
the default user-home directory for this entire process.
"""

from __future__ import annotations

import os
import sys


def _apply_early_cli_environment(argv: list[str]) -> None:
    for index, value in enumerate(argv):
        if value == "--root" and index + 1 < len(argv):
            os.environ["CLOUD_ROOT_DIR"] = argv[index + 1]
        elif value.startswith("--root="):
            os.environ["CLOUD_ROOT_DIR"] = value.split("=", 1)[1]
        elif value == "--name" and index + 1 < len(argv):
            os.environ["CLOUD_SLAVE_NAME"] = argv[index + 1]
        elif value.startswith("--name="):
            os.environ["CLOUD_SLAVE_NAME"] = value.split("=", 1)[1]


_apply_early_cli_environment(sys.argv[1:])

# Keep this visible static import so PyInstaller packages configuration even
# though ``slave.main`` reloads it later after parsing its full argument set.
import slave.config as _slave_config  # noqa: E402,F401
from slave.main import main  # noqa: E402  (intentional import ordering)


if __name__ == "__main__":
    main()
