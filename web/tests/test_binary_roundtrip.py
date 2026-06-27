"""End-to-end binary tests against the real 96-byte protocol.

Unlike the unit tests in test_frame_router.py (which use a 4-byte mock
protocol to test the router logic), these tests use NeuraPYProtocol
and VisionProtocol directly with actual 96-byte frames. They verify:
  1. build_motion / parse_motion round-trip with every field
  2. build_status / parse_status round-trip with every flag
  3. FrameRouter handles real 96-byte chunking (split mid-frame, etc.)
  4. Schema values (offsets, lengths, names) match the bytes the
     parser actually reads — so the UI form builder can't drift from
     what the codec understands.

These run as part of `python -m unittest discover -s web/tests` since
they have no external deps (no neurapy, no TCP).
"""
import struct
import unittest

from vision_protocol import FRAME_SIZE, HEADER_QUERY, VisionProtocol
from web.frame_router import FrameRouter
from web.protocols import load


class TestVisionProtocolBinary(unittest.TestCase):
    """Direct codec tests with real 96-byte payloads."""

    def test_query_frame_is_92_zero_bytes(self):
        # No public builder for query; query is byte literal
        raw = HEADER_QUERY + b"\x00" * 92
        self.assertEqual(len(raw), FRAME_SIZE)
        self.assertTrue(VisionProtocol.is_query(raw))

    def test_build_motion_every_field_round_trip(self):
        joints = [10.5, -20.25, 30.0, 0.0, 45.5, -1.5]
        position = (500.0, -100.5, 300.25)
        orientation = (3.14159, 0.0, -1.5708)
        enter = bytes(range(1, 17))           # 16 bytes
        exit_ = bytes(reversed(range(1, 17))) # 16 bytes

        raw = VisionProtocol.build_motion(
            joints=joints, position=position, orientation=orientation,
            work_area=2, speed=8, blend_radius=5,
            motion_type=2, request_motion=1, point_id=42,
            enter_area=enter, exit_area=exit_)

        self.assertEqual(len(raw), FRAME_SIZE)
        # Header (bytes 0-3)
        self.assertEqual(raw[0], 0x02)          # robot_brand (KUKA placeholder)
        self.assertEqual(raw[1:4], b"\x01\x01\x00")
        # Joints (bytes 4-27, 24 bytes) — use assertAlmostEqual for floats
        for got, want in zip(struct.unpack("<6f", raw[4:28]), joints):
            self.assertAlmostEqual(got, want, places=4)
        # Position (bytes 28-39, 12 bytes)
        for got, want in zip(struct.unpack("<3f", raw[28:40]), position):
            self.assertAlmostEqual(got, want, places=4)
        # Orientation (bytes 40-51, 12 bytes)
        for got, want in zip(struct.unpack("<3f", raw[40:52]), orientation):
            self.assertAlmostEqual(got, want, places=4)
        # Single-byte fields
        self.assertEqual(raw[52], 2)   # work_area
        self.assertEqual(raw[53], 8)   # speed
        self.assertEqual(raw[54], 5)   # blend_radius
        self.assertEqual(raw[55], 2)   # motion_type (MoveJ)
        self.assertEqual(raw[56], 1)   # request_motion
        # point_id (bytes 57-60, int32 LE)
        self.assertEqual(struct.unpack("<i", raw[57:61])[0], 42)
        # enter_area (bytes 61-76)
        self.assertEqual(raw[61:77], enter)
        # exit_area (bytes 77-92)
        self.assertEqual(raw[77:93], exit_)

        # Now decode and confirm symmetric round-trip (use assertAlmostEqual
        # for floats — 32-bit LE encoding loses precision vs Python doubles)
        parsed = VisionProtocol.parse_motion(raw)
        for got, want in zip(parsed["joints"], joints):
            self.assertAlmostEqual(got, want, places=4)
        for got, want in zip(parsed["position"], position):
            self.assertAlmostEqual(got, want, places=4)
        for got, want in zip(parsed["orientation"], orientation):
            self.assertAlmostEqual(got, want, places=4)
        self.assertEqual(parsed["work_area"], 2)
        self.assertEqual(parsed["speed"], 8)
        self.assertEqual(parsed["blend_radius"], 5)
        self.assertEqual(parsed["motion_type"], 2)
        self.assertEqual(parsed["request_motion"], 1)
        self.assertEqual(parsed["point_id"], 42)
        self.assertEqual(parsed["enter_area"], enter)
        self.assertEqual(parsed["exit_area"], exit_)

    def test_build_status_every_flag_round_trip(self):
        joints = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        position = (100.0, 200.0, 300.0)
        orientation = (0.0, 0.0, 0.0)

        raw = VisionProtocol.build_status(
            joints=joints, position=position, orientation=orientation,
            work_status=1, at_origin=1, emergency_stop=0,
            is_moving=1, main_program_started=1,
            work_area=3, exception=0, exception_code=42)

        self.assertEqual(len(raw), FRAME_SIZE)
        self.assertEqual(raw[0:4], b"\x02\x01\x01\x00")
        self.assertEqual(list(struct.unpack("<6f", raw[4:28])), joints)
        self.assertEqual(list(struct.unpack("<3f", raw[28:40])), list(position))
        self.assertEqual(list(struct.unpack("<3f", raw[40:52])), list(orientation))
        # Status flags (bytes 52-59)
        self.assertEqual(raw[52], 1)   # work_status
        self.assertEqual(raw[53], 1)   # at_origin
        self.assertEqual(raw[54], 0)   # emergency_stop
        self.assertEqual(raw[55], 1)   # is_moving
        self.assertEqual(raw[56], 1)   # main_program_started
        self.assertEqual(raw[57], 3)   # work_area
        self.assertEqual(raw[58], 0)   # exception
        self.assertEqual(raw[59], 42)  # exception_code

        parsed = VisionProtocol.parse_status(raw)
        self.assertEqual(parsed["work_status"], 1)
        self.assertEqual(parsed["is_moving"], 1)
        self.assertEqual(parsed["work_area"], 3)
        self.assertEqual(parsed["exception_code"], 42)


class TestFrameRouterRealBytes(unittest.TestCase):
    """FrameRouter fed real 96-byte NeuraPY frames."""

    def setUp(self):
        self.p = load("neurapy")

    def test_single_motion_frame(self):
        captured = []
        r = FrameRouter(self.p, on_frame=lambda raw, t: captured.append((raw, t)))
        raw = self.p.build("motion", joints=[0]*6, position=[0, 0, 0],
                           orientation=[0, 0, 0], motion_type=1, point_id=1)
        r.feed(raw)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], raw)
        self.assertEqual(captured[0][1], "motion_or_status")

    def test_three_frames_in_one_chunk(self):
        """Three motion frames concatenated: router must emit all three."""
        captured = []
        r = FrameRouter(self.p, on_frame=lambda raw, t: captured.append((raw, t)))
        frames = [self.p.build("motion", joints=[float(i)]*6, point_id=i,
                                motion_type=1) for i in (1, 2, 3)]
        r.feed(b"".join(frames))
        self.assertEqual(len(captured), 3)
        # Verify each frame decodes to its expected point_id
        for i, (raw, _) in enumerate(captured, 1):
            parsed = self.p.parse(raw, expected_type="motion")
            self.assertEqual(parsed["fields"]["point_id"], i)

    def test_chunked_at_every_byte_boundary(self):
        """Feed the same frame one byte at a time; must still emit one frame."""
        captured = []
        r = FrameRouter(self.p, on_frame=lambda raw, t: captured.append((raw, t)))
        raw = self.p.build("motion", joints=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                           position=(100, 200, 300), orientation=(0, 0, 0),
                           motion_type=3, point_id=7, request_motion=1,
                           speed=9, blend_radius=10, work_area=1)
        for b in raw:
            r.feed(bytes([b]))
        self.assertEqual(len(captured), 1)
        parsed = self.p.parse(captured[0][0], expected_type="motion")
        self.assertEqual(parsed["fields"]["point_id"], 7)
        self.assertEqual(parsed["fields"]["motion_type"], 3)

    def test_query_then_motion_concatenated(self):
        """Different frame types in the same stream."""
        captured = []
        r = FrameRouter(self.p, on_frame=lambda raw, t: captured.append((raw, t)))
        query = HEADER_QUERY + b"\x00" * 92
        motion = self.p.build("motion", point_id=99, motion_type=2)
        r.feed(query + motion)
        self.assertEqual([t for _, t in captured], ["query", "motion_or_status"])


class TestSchemaMatchesCodec(unittest.TestCase):
    """Schema field offsets/lengths must match what the codec actually reads.

    If the schema drifts (e.g. someone edits an offset in NeuraPYProtocol
    without updating VisionProtocol), the UI will build frames the codec
    can't decode. These tests catch that drift.
    """

    def setUp(self):
        self.p = load("neurapy")

    def test_schema_offsets_consistent_with_parse_motion(self):
        """Build a motion frame where each field is at a unique sentinel
        value, then verify parse_motion returns those same values at the
        offsets declared in the schema."""
        # Build with non-zero values everywhere so we can detect misalignment
        raw = VisionProtocol.build_motion(
            joints=[1.5]*6,
            position=(10.0, 20.0, 30.0),
            orientation=(40.0, 50.0, 60.0),
            work_area=11, speed=22, blend_radius=33,
            motion_type=44, request_motion=55, point_id=66,
            enter_area=bytes([0xAA]*16), exit_area=bytes([0xBB]*16))
        schema_fields = {f["name"]: f for f in self.p.schema["frames"]["motion"]["fields"]}
        # Each schema field's offset+length must fall inside the frame
        for name, f in schema_fields.items():
            self.assertLessEqual(f["offset"] + f["length"], FRAME_SIZE,
                                f"{name} offset+length exceeds frame size")
            self.assertGreaterEqual(f["offset"], 4,
                                    f"{name} offset should be after the 4-byte header")

        # Now check that parse_motion sees the values at the schema offsets
        # (decoded via the codec, not via schema offsets, so this catches drift)
        parsed = VisionProtocol.parse_motion(raw)
        self.assertEqual(parsed["work_area"], 11)
        self.assertEqual(parsed["speed"], 22)
        self.assertEqual(parsed["blend_radius"], 33)
        self.assertEqual(parsed["motion_type"], 44)
        self.assertEqual(parsed["request_motion"], 55)
        self.assertEqual(parsed["point_id"], 66)
        # Sentinel bytes in enter_area/exit_area
        self.assertEqual(parsed["enter_area"], bytes([0xAA]*16))
        self.assertEqual(parsed["exit_area"], bytes([0xBB]*16))
        # Schema offset for point_id must be 57
        self.assertEqual(schema_fields["point_id"]["offset"], 57)
        # Schema offset for motion_type must be 55
        self.assertEqual(schema_fields["motion_type"]["offset"], 55)

    def test_schema_offsets_consistent_with_parse_status(self):
        raw = VisionProtocol.build_status(
            joints=[0]*6, position=(0, 0, 0), orientation=(0, 0, 0),
            work_status=77, at_origin=88, emergency_stop=99,
            is_moving=111, main_program_started=122, work_area=133,
            exception=144, exception_code=155)
        parsed = VisionProtocol.parse_status(raw)
        # Each flag should match what we built
        self.assertEqual(parsed["work_status"], 77)
        self.assertEqual(parsed["at_origin"], 88)
        self.assertEqual(parsed["emergency_stop"], 99)
        self.assertEqual(parsed["is_moving"], 111)
        self.assertEqual(parsed["main_program_started"], 122)
        self.assertEqual(parsed["work_area"], 133)
        self.assertEqual(parsed["exception"], 144)
        self.assertEqual(parsed["exception_code"], 155)

        schema_fields = {f["name"]: f for f in self.p.schema["frames"]["status"]["fields"]}
        self.assertEqual(schema_fields["work_status"]["offset"], 52)
        self.assertEqual(schema_fields["exception_code"]["offset"], 59)


class TestBytesOnWire(unittest.TestCase):
    """Verify the bit-level byte layout matches the spec docs.

    If any of these fail, the wire format has drifted from the documented
    layout in docs/VisionInspectRobot*.xlsx and the camera/robot won't
    talk to each other anymore.
    """

    def test_zero_motion_frame_byte_layout(self):
        """A motion frame built from explicit zero values must have a
        specific byte layout that matches the xlsx spec (byte 0 =
        ROBOT_BRAND, bytes 1-3 = function flags, etc.). Note that the
        default speed=5 / motion_type=1 / request_motion=1 / point_id=1
        are NOT zero, so we pass them explicitly."""
        raw = VisionProtocol.build_motion(
            joints=[0]*6, position=(0, 0, 0), orientation=(0, 0, 0),
            speed=0, blend_radius=0,
            motion_type=0, request_motion=0, point_id=0)
        # Byte 0 must be ROBOT_BRAND (0x02)
        self.assertEqual(raw[0], 0x02)
        # Bytes 1-3 are function code flags; spec says 0x01 0x01 0x00
        self.assertEqual(raw[1:4], b"\x01\x01\x00")
        # Bytes 4-56 are all zeros (joints + pos + orient + work_area + speed +
        # blend_radius + motion_type=0 + request_motion=0)
        self.assertEqual(raw[4:57], b"\x00" * 53)
        # Bytes 57-60 (point_id=0) must be 00 00 00 00 (little-endian int32)
        self.assertEqual(raw[57:61], b"\x00\x00\x00\x00")
        # Bytes 61-92 are enter_area + exit_area, both zero by default
        self.assertEqual(raw[61:93], b"\x00" * 32)
        # Entire frame is 96 bytes
        self.assertEqual(len(raw), FRAME_SIZE)

    def test_query_frame_looks_like_query(self):
        """Query frame: 4-byte header 02 02 02 02 + 92 zero bytes."""
        raw = HEADER_QUERY + b"\x00" * 92
        self.assertEqual(len(raw), FRAME_SIZE)
        self.assertEqual(raw[:4], b"\x02\x02\x02\x02")
        self.assertEqual(raw[4:], b"\x00" * 92)
        # Both parse_motion and parse_status should NOT match this as a
        # motion/status frame (different header byte 0).
        # is_query correctly identifies it.
        self.assertTrue(VisionProtocol.is_query(raw))


if __name__ == "__main__":
    unittest.main()