import json
import socket
import threading
import time
import unittest
import urllib.request
from web.protocol import Protocol
from web.server import make_server
from web.state import Event, EventBus


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


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.status, r.read().decode("utf-8")


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # surface non-2xx as a normal (status, body) so tests can assert on it
        return e.code, e.read().decode("utf-8")


class TestServer(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.proto = _P()
        self.port = _free_port()
        self.server = make_server(self.proto, self.bus, host="127.0.0.1", port=self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def test_snapshot(self):
        self.bus.push(Event(ts=1.0, kind="x", src="y", data={"k": 1}))
        s, b = _get(f"http://127.0.0.1:{self.port}/api/snapshot")
        j = json.loads(b)
        self.assertEqual(len(j["events"]), 1)
        self.assertEqual(j["events"][0]["data"]["k"], 1)

    def test_schema(self):
        s, b = _get(f"http://127.0.0.1:{self.port}/api/schema")
        j = json.loads(b)
        self.assertIn("frames", j)
        self.assertIn("X", j["frames"])

    def test_build(self):
        s, b = _post(f"http://127.0.0.1:{self.port}/api/build",
                      {"type": "X", "fields": {}})
        j = json.loads(b)
        self.assertEqual(j["hex"], "00000000")
        self.assertEqual(j["len"], 4)

    def test_parse(self):
        s, b = _post(f"http://127.0.0.1:{self.port}/api/parse", {"hex": "aabbccdd"})
        j = json.loads(b)
        self.assertEqual(j["type"], "X")
        self.assertEqual(j["len"], 4)

    def test_connect_disconnect_fake_camera(self):
        # /api/connect should create + start a FakeCamera; /api/disconnect should stop it
        self.assertIsNone(self.server._state.fake_camera)
        port = _free_port()
        s, b = _post(f"http://127.0.0.1:{self.port}/api/connect",
                      {"role": "fake_camera", "port": port})
        self.assertEqual(s, 200)
        j = json.loads(b)
        self.assertTrue(j["ok"])
        self.assertIsNotNone(self.server._state.fake_camera)
        # double-connect should refuse
        s2, _ = _post(f"http://127.0.0.1:{self.port}/api/connect",
                       {"role": "fake_camera", "port": _free_port()})
        self.assertEqual(s2, 409)
        # disconnect
        s3, b3 = _post(f"http://127.0.0.1:{self.port}/api/disconnect",
                        {"role": "fake_camera"})
        self.assertEqual(s3, 200)
        self.assertIsNone(self.server._state.fake_camera)
        # double-disconnect should refuse
        s4, _ = _post(f"http://127.0.0.1:{self.port}/api/disconnect",
                       {"role": "fake_camera"})
        self.assertEqual(s4, 409)

    def test_stream_emits_event(self):
        received = []
        stop = threading.Event()

        def consume():
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/stream")
            with urllib.request.urlopen(req, timeout=3) as r:
                buf = b""
                while not stop.is_set():
                    chunk = r.read(1)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n\n" in buf:
                        line, _, buf = buf.partition(b"\n\n")
                        if line.startswith(b"data: "):
                            received.append(json.loads(line[6:].decode()))
                        if received:
                            break

        t = threading.Thread(target=consume, daemon=True)
        t.start()
        time.sleep(0.1)
        self.bus.push(Event(ts=2.0, kind="new", src="test", data={"v": 42}))
        t.join(timeout=2.0)
        stop.set()
        self.assertTrue(any(e["kind"] == "new" for e in received), received)

    def test_connect_disconnect_inspector(self):
        # /api/connect with role=inspector should start an InspectorClient
        self.assertIsNone(self.server._state.inspector)
        s, b = _post(f"http://127.0.0.1:{self.port}/api/connect",
                      {"role": "inspector", "host": "127.0.0.1", "port": 65535})
        # 200 = inspector thread spawned (will fail to connect 65535, that's fine)
        # 503 = OSError during connect (unusual; we don't expect this here)
        self.assertIn(s, (200, 503))
        j = json.loads(b)
        if s == 200:
            self.assertEqual(j["ok"], True)
            self.assertIsNotNone(self.server._state.inspector)
            try:
                # double-connect should refuse
                s2, _ = _post(f"http://127.0.0.1:{self.port}/api/connect",
                               {"role": "inspector", "host": "127.0.0.1", "port": 65535})
                self.assertEqual(s2, 409)
                # disconnect
                s3, _ = _post(f"http://127.0.0.1:{self.port}/api/disconnect",
                               {"role": "inspector"})
                self.assertEqual(s3, 200)
                self.assertIsNone(self.server._state.inspector)
            finally:
                # belt-and-suspenders: if anything failed mid-test, still stop
                if self.server._state.inspector is not None:
                    self.server._state.inspector.stop()
                    self.server._state.inspector = None

    def test_connect_unknown_role(self):
        s, b = _post(f"http://127.0.0.1:{self.port}/api/connect",
                      {"role": "bogus"})
        self.assertEqual(s, 400)
        self.assertIn("unknown role", json.loads(b)["error"])

