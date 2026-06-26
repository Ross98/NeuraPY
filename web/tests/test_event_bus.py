import threading
import time
import unittest
from web.state import Event, EventBus


class TestEventBus(unittest.TestCase):
    def test_event_dataclass(self):
        e = Event(ts=1.5, kind="frame_in", src="fake_camera", data={"x": 1})
        self.assertEqual(e.ts, 1.5)
        self.assertEqual(e.kind, "frame_in")
        self.assertEqual(e.data, {"x": 1})

    def test_push_then_snapshot(self):
        bus = EventBus()
        bus.push(Event(ts=1.0, kind="x", src="y", data={"k": "v"}))
        snap = bus.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0], {"ts": 1.0, "kind": "x", "src": "y", "data": {"k": "v"}})

    def test_snapshot_capped_at_200(self):
        bus = EventBus()
        for i in range(250):
            bus.push(Event(ts=float(i), kind="x", src="y", data={}))
        snap = bus.snapshot()
        self.assertEqual(len(snap), 200)
        self.assertEqual(snap[0]["ts"], 50.0)
        self.assertEqual(snap[-1]["ts"], 249.0)

    def test_subscribe_receives_pushed_events(self):
        bus = EventBus()
        received = []
        def consumer():
            for e in bus.subscribe():
                received.append(e)
                if len(received) >= 2: return
        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        time.sleep(0.05)
        bus.push(Event(ts=1.0, kind="x", src="y", data={}))
        bus.push(Event(ts=2.0, kind="x", src="y", data={}))
        t.join(timeout=1.0)
        self.assertEqual(len(received), 2)
        self.assertEqual([e.ts for e in received], [1.0, 2.0])

    def test_queue_full_drops_oldest(self):
        bus = EventBus(maxlen=2)
        alive = threading.Event()
        def keep():
            alive.set()
            for _ in bus.subscribe(): pass
        t = threading.Thread(target=keep, daemon=True)
        t.start()
        alive.wait(0.1)
        for i in range(5):
            bus.push(Event(ts=float(i), kind="x", src="y", data={}))
        time.sleep(0.1)
        self.assertTrue(t.is_alive())
