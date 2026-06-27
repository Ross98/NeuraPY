"""In-process closed loop: motion frame build -> feed -> parse -> round-trip."""
import unittest
from web.frame_router import FrameRouter
from web.protocols import load


class TestE2E(unittest.TestCase):
    def test_motion_frame_round_trip(self):
        p = load("neurapy")
        events = []
        r = FrameRouter(p, on_frame=lambda raw, t: events.append((raw, t)))
        raw = p.build("motion",
                      joints=[10, 20, 30, 0, 40, 0],
                      position=[500, 0, 200],
                      orientation=[0, 0, 0],
                      motion_type=1, point_id=2, speed=5)
        self.assertEqual(len(raw), p.FRAME_SIZE)
        r.feed(raw)
        self.assertEqual(len(events), 1)
        raw_back, t = events[0]
        # FrameRouter can't know motion vs status (same header), so
        # classify() returns motion_or_status. Caller passes expected_type
        # to disambiguate.
        self.assertEqual(t, "motion_or_status")
        parsed = p.parse(raw_back, expected_type="motion")
        self.assertEqual(parsed["type"], "motion")
        rt = parsed["fields"].get("joints")
        if rt is not None:
            for a, b in zip(rt, [10.0, 20.0, 30.0, 0.0, 40.0, 0.0]):
                self.assertAlmostEqual(a, b, places=3)

    def test_query_frame(self):
        p = load("neurapy")
        captured = []
        r = FrameRouter(p, on_frame=lambda raw, t: captured.append((raw, t)))
        raw = b"\x02\x02\x02\x02" + b"\x00" * 92
        r.feed(raw)
        self.assertEqual(captured, [(raw, "query")])
