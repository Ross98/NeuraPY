import unittest
from web.frame_router import FrameRouter
from web.protocol import Protocol


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


class TestFrameRouter(unittest.TestCase):
    def test_complete_frame_emitted(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\x01\x02\x03\x04")
        self.assertEqual(frames, [(b"\x01\x02\x03\x04", "X")])

    def test_chunked_frame_accumulated(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\x01\x02"); r.feed(b"\x03\x04")
        self.assertEqual(frames, [(b"\x01\x02\x03\x04", "X")])

    def test_multiple_frames_in_one_chunk(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\x01\x02\x03\x04\x05\x06\x07\x08")
        self.assertEqual(len(frames), 2)

    def test_oversize_buffer_keeps_tail(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][0], b"\x01\x02\x03\x04")
        self.assertEqual(frames[1][0], b"\x05\x06\x07\x08")
