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

    def test_two_cameras_do_not_share_clients(self):
        """Regression: _Handler.cam was a class attribute, so starting a 2nd
        FakeCamera reassigned it and stole the 1st camera's connections."""
        bus1, bus2 = EventBus(), EventBus()
        port1, port2 = _free_port(), _free_port()
        cam1 = FakeCamera(_P(), bus1, host="127.0.0.1", port=port1)
        cam2 = FakeCamera(_P(), bus2, host="127.0.0.1", port=port2)
        cam1.start(); cam2.start()
        try:
            s1 = socket.socket(); s1.connect(("127.0.0.1", port1))
            s2 = socket.socket(); s2.connect(("127.0.0.1", port2))
            time.sleep(0.15)
            # cam1's client belongs to cam1, cam2's to cam2
            self.assertEqual(cam1.peer_count, 1)
            self.assertEqual(cam2.peer_count, 1)
            cam1.send_bytes(b"\x01\x02\x03\x04")
            time.sleep(0.15)
            # cam1 broadcast → s1 gets it; s2 does NOT
            self.assertEqual(s1.recv(4), b"\x01\x02\x03\x04")
            self.assertEqual(s2.recv(0), b"")  # non-blocking peek would be better but recv(0) returns immediately
            s1.close(); s2.close()
        finally:
            cam1.stop(); cam2.stop()
