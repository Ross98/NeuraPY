import socket
import threading
import time
import unittest
from web.protocol import Protocol
from web.roles.inspector_client import InspectorClient
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


class TestInspector(unittest.TestCase):
    def test_receives_frames(self):
        bus = EventBus()
        port = _free_port()
        ss = socket.socket()
        ss.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ss.bind(("127.0.0.1", port)); ss.listen(1)

        def server():
            conn, _ = ss.accept()
            conn.sendall(b"\x01\x02\x03\x04")
            time.sleep(0.05)
            conn.close()

        threading.Thread(target=server, daemon=True).start()
        ic = InspectorClient(_P(), bus, host="127.0.0.1", port=port)
        ic.start()
        time.sleep(0.3)
        ic.stop()
        ss.close()
        self.assertIn("frame_in", [e["kind"] for e in bus.snapshot()])

    def test_retries_on_failure(self):
        bus = EventBus()
        ic = InspectorClient(_P(), bus, host="127.0.0.1", port=1,
                             retry_initial=0.05, retry_max=0.1, retry_max_attempts=2)
        ic.start()
        time.sleep(0.5)
        ic.stop()
        msgs = [e["data"].get("msg", "") for e in bus.snapshot() if e["kind"] == "log"]
        self.assertTrue(any("retry" in m or "failed" in m for m in msgs), msgs)

    def test_recv_timeout_default_matches_point_client(self):
        # Default 60s so inspector survives normal frame gaps from real cameras
        ic = InspectorClient(_P(), EventBus(), host="127.0.0.1", port=1)
        self.assertEqual(ic._recv_timeout, 60.0)

