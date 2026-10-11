import sys
from cloud_pc.shared_components.common.release import __version__

if __name__ == "__main__":
    if '--version' in sys.argv:
        print(__version__)
    elif '--smoke-test' in sys.argv:
        from cloud_pc.management_ui.verify_windows_release import main
        main()
    else:
        from cloud_pc.management_ui.windows_main import main
        main()
