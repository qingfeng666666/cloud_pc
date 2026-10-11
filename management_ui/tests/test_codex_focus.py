import unittest
from AR.alxr_integration.screenshot_relay import _focus_is_within


class Tree:
    def XDefaultRootWindow(self, handle):
        return 99

    def XQueryTree(self, handle, window, root, parent, children, count):
        parents = {11: 10, 21: 20, 20: 99}
        if window not in parents:
            return 0
        parent._obj.value = parents[window]
        return 1


class FocusTests(unittest.TestCase):
    def test_target_and_its_child(self):
        self.assertTrue(_focus_is_within(Tree(), None, 10, 10))
        self.assertTrue(_focus_is_within(Tree(), None, 11, 10))

    def test_other_app_child_is_rejected(self):
        self.assertFalse(_focus_is_within(Tree(), None, 21, 10))

    def test_root_unavailable_and_special_focus_rejected(self):
        for window in (0, 1, 99, 88):
            self.assertFalse(_focus_is_within(Tree(), None, window, 10))
