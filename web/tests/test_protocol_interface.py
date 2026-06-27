import unittest
from web.protocol import FRAME_DIRECTIONS, Protocol


class TestProtocolABC(unittest.TestCase):
    def test_cannot_instantiate_directly(self):
        with self.assertRaises(TypeError):
            Protocol()

    def test_must_implement_all_methods(self):
        class Incomplete(Protocol):
            FRAME_SIZE = 10
            def classify(self, frame): return "x"
        with self.assertRaises(TypeError):
            Incomplete()

    def test_subclass_with_all_methods_ok(self):
        class Full(Protocol):
            FRAME_SIZE = 10
            def classify(self, frame): return "x"
            def parse(self, frame): return {"type": "x", "fields": {}}
            def build(self, type, **f): return b"\\x00" * 10
            @property
            def schema(self): return {"frames": {}}
        p = Full()
        self.assertEqual(p.FRAME_SIZE, 10)
        self.assertIsInstance(p, Protocol)

    def test_direction_values_are_frozen(self):
        """The set of valid direction values must be frozen so a typo
        like 'camera_2_robot' or 'robot_to_cam' is caught at schema
        validation time, not silently ignored by the frontend."""
        self.assertEqual(FRAME_DIRECTIONS,
                         frozenset({"camera_to_robot", "robot_to_camera"}))
