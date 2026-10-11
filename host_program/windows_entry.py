"""Windows host executable is a service, independent of the management UI."""
import sys
from cloud_pc.host_program.main import main

if __name__ == "__main__":
    raise SystemExit(main())
