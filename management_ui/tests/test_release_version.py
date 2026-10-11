"""Keep platform product versions aligned without changing wire/dependency versions."""
import re
import unittest
from pathlib import Path

from cloud_pc.shared_components.common.release import __version__


class ReleaseVersionTest(unittest.TestCase):
    def test_android_versions_match(self):
        root = Path(__file__).resolve().parents[2]
        for component in ('android_slave', 'management_android'):
            with self.subTest(component=component):
                text = (root / component / 'app/build.gradle.kts').read_text()
                self.assertEqual(re.search(r'versionName\s*=\s*"([^"]+)"', text).group(1), __version__)
                self.assertEqual(int(re.search(r'versionCode\s*=\s*(\d+)', text).group(1)), 1)

    def test_windows_resource_matches(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / 'shared_components/windows_version_info.txt').read_text()
        self.assertIn("StringStruct('FileVersion', '" + __version__ + "')", text)
        self.assertIn("StringStruct('ProductVersion', '" + __version__ + "')", text)


if __name__ == '__main__':
    unittest.main()
