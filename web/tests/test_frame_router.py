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

    def test_on_error_called_when_consumer_raises(self):
        """Regression: consumer exceptions were silently swallowed — debug
        UI hid the bug. Router must invoke on_error so caller can log it."""
        errors = []

        def bad_consumer(f, t):
            raise RuntimeError("boom")

        r = FrameRouter(_P(), on_frame=bad_consumer,
                        on_error=lambda frame, t, err: errors.append((frame, t, str(err))))
        r.feed(b"\x01\x02\x03\x04")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], b"\x01\x02\x03\x04")
        self.assertEqual(errors[0][1], "X")
        self.assertIn("boom", errors[0][2])

    def test_on_error_optional_defaults_to_silent(self):
        """Backward compat: callers that don't pass on_error keep old behavior."""
        def bad_consumer(f, t):
            raise RuntimeError("boom")

        r = FrameRouter(_P(), on_frame=bad_consumer)  # no on_error
        # Should not raise
        r.feed(b"\x01\x02\x03\x04")

    def test_flush_partial_discards_stale_buffer(self):
        """Regression: partial_timeout was a dead param. If a TCP client
        disconnects mid-frame, the leftover bytes corrupt the next session
        on the same connection. flush_partial() drops bytes older than
        `partial_timeout` seconds."""
        from web.frame_router import FrameRouter
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)),
                        partial_timeout=0.1)
        r.feed(b"\x01\x02")  # 2 bytes, no complete frame yet
        # Before timeout: nothing flushed
        r.flush_partial(now=r._last_feed + 0.05)
        self.assertEqual(len(r._buf), 2)
        # After timeout: dropped
        r.flush_partial(now=r._last_feed + 0.5)
        self.assertEqual(len(r._buf), 0)

    def test_flush_partial_keeps_fresh_buffer(self):
        r = FrameRouter(_P(), on_frame=lambda *_: None, partial_timeout=0.1)
        r.feed(b"\x01\x02")
        r.flush_partial(now=r._last_feed + 0.05)
        self.assertEqual(len(r._buf), 2)
