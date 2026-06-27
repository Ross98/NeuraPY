"""Schema completeness for NeuraPYProtocol — UI coverage of the full 96-byte
spec, not just the happy path."""
import unittest
from web.protocols import load


class TestNeuraPYSchema(unittest.TestCase):
    def setUp(self):
        self.p = load("neurapy")

    def _field_names(self, frame_type):
        return {f["name"] for f in self.p.schema["frames"][frame_type]["fields"]}

    def test_motion_schema_includes_area_and_request(self):
        names = self._field_names("motion")
        for required in ("enter_area", "exit_area", "request_motion",
                         "motion_type", "speed", "blend_radius", "point_id",
                         "work_area", "joints", "position", "orientation"):
            self.assertIn(required, names,
                          f"motion schema missing field {required!r}")

    def test_status_schema_includes_all_flags(self):
        names = self._field_names("status")
        for required in ("is_moving", "at_origin", "emergency_stop",
                         "main_program_started", "work_status",
                         "work_area", "exception", "exception_code"):
            self.assertIn(required, names,
                          f"status schema missing field {required!r}")

    def test_frame_direction_declared(self):
        """motion = camera→robot, status = robot→camera. Frontend uses this
        to pick the right send-target and color frames by direction."""
        for ftype, expected in (("query", "camera_to_robot"),
                                ("motion", "camera_to_robot"),
                                ("status", "robot_to_camera")):
            entry = self.p.schema["frames"][ftype]
            self.assertIn("direction", entry, f"{ftype} missing direction")
            self.assertEqual(entry["direction"], expected,
                             f"{ftype} direction should be {expected}")

    def test_build_motion_round_trip_with_areas(self):
        """Building a motion frame with enter_area/exit_area must round-trip."""
        enter = bytes(range(1, 17))           # 1..16
        exit_ = bytes(range(16, 0, -1))       # 16..1
        raw = self.p.build("motion",
                           joints=[0]*6, position=[0, 0, 0], orientation=[0, 0, 0],
                           enter_area=enter, exit_area=exit_,
                           motion_type=1, point_id=1, request_motion=1)
        self.assertEqual(len(raw), self.p.FRAME_SIZE)
        parsed = self.p.parse(raw, expected_type="motion")
        self.assertEqual(parsed["type"], "motion")
        self.assertEqual(parsed["fields"]["enter_area"], enter)
        self.assertEqual(parsed["fields"]["exit_area"], exit_)

    def test_build_status_round_trip_with_flags(self):
        raw = self.p.build("status",
                           joints=[0]*6, position=[0, 0, 0], orientation=[0, 0, 0],
                           is_moving=1, at_origin=0, emergency_stop=0,
                           main_program_started=1, work_status=1,
                           work_area=2, exception=0, exception_code=0)
        self.assertEqual(len(raw), self.p.FRAME_SIZE)
        parsed = self.p.parse(raw, expected_type="status")
        self.assertEqual(parsed["type"], "status")
        self.assertEqual(parsed["fields"]["is_moving"], 1)
        self.assertEqual(parsed["fields"]["work_status"], 1)
        self.assertEqual(parsed["fields"]["main_program_started"], 1)
        self.assertEqual(parsed["fields"]["work_area"], 2)

    def test_parse_without_hint_returns_motion_or_status(self):
        """motion + status share header 02 01 01 00. Without an expected_type
        hint, the parser must return both layouts so the caller can pick."""
        raw = self.p.build("motion", joints=[0]*6, position=[0, 0, 0],
                           orientation=[0, 0, 0], motion_type=1, point_id=1)
        parsed = self.p.parse(raw)
        self.assertEqual(parsed["type"], "motion_or_status")
        self.assertIn("motion", parsed["fields"])
        self.assertIn("status", parsed["fields"])

    def test_direction_values_are_valid(self):
        """If a protocol declares direction, it must use one of the
        frozen values. Anything else is a typo that the UI won't handle."""
        from web.protocol import FRAME_DIRECTIONS
        for ftype, entry in self.p.schema["frames"].items():
            if "direction" in entry:
                self.assertIn(entry["direction"], FRAME_DIRECTIONS,
                              f"{ftype} has invalid direction {entry['direction']!r}")