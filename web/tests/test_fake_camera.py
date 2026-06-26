import socket
import time
import unittest
from web.protocol import Protocol
from web.roles.fake_camera import FakeCamera
from web.state import EventBus


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class TestFakeCamera(unittest.TestCase):
    def test_client_can_connect_and_frame_in_event(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s = socket.socket(); s.connect(("127.0.0.1", port))
            s.sendall(b"\xAA\xBB\xCC\xDD")
            time.sleep(0.15)
            self.assertTrue(any(e["kind"] == "frame_in" for e in bus.snapshot()))
            s.close()
        finally:
            cam.stop()

    def test_send_bytes_broadcasts(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s1 = socket.socket(); s1.connect(("127.0.0.1", port))
            s2 = socket.socket(); s2.connect(("127.0.0.1", port))
            time.sleep(0.1)
            cam.send_bytes(b"\x01\x02\x03\x04")
            time.sleep(0.15)
            self.assertEqual(s1.recv(4), b"\x01\x02\x03\x04")
            self.assertEqual(s2.recv(4), b"\x01\x02\x03\x04")
            s1.close(); s2.close()
        finally:
            cam.stop()

    def test_connect_disconnect_events(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s = socket.socket(); s.connect(("127.0.0.1", port))
            time.sleep(0.15)
            kinds = [e["kind"] for e in bus.snapshot()]
            self.assertIn("connect", kinds)
            s.close()
            time.sleep(0.15)
            kinds = [e["kind"] for e in bus.snapshot()]
            self.assertIn("disconnect", kinds)
        finally:
            cam.stop()
