"""TCP client that passively captures frames from a real camera / point_client."""
import socket
import threading
import time
from web.frame_router import FrameRouter
from web.protocol import Protocol
from web.state import Event, EventBus


class InspectorClient:
    def __init__(self, protocol: Protocol, event_bus: EventBus, host: str, port: int,
                 retry_initial: float = 1.0, retry_max: float = 30.0,
                 retry_max_attempts: int = 0):
        self.protocol = protocol
        self.bus = event_bus
        self.host = host
        self.port = port
        self._retry = retry_initial
        self._retry_max = retry_max
        self._retry_max_attempts = retry_max_attempts
        self._stop = threading.Event()
        self._thread = None
        self._attempts = 0

        def on_frame(raw, t):
            # Camera always sends motion frames (camera→robot direction).
            # If your camera ever sends status, route it through the
            # server.py /api/send path or a separate role instead.
            try:
                parsed = self.protocol.parse(raw, expected_type="motion")
            except Exception as e:
                parsed = {"type": "unknown", "fields": {}, "error": str(e)}
            self.bus.push(Event(ts=time.time(), kind="frame_in", src="inspector",
                                data={"raw_hex": raw.hex(), "len": len(raw),
                                      "parsed": parsed,
                                      "peer": f"{self.host}:{self.port}"}))

        def on_error(raw, t, err):
            self.bus.push(Event(ts=time.time(), kind="error", src="inspector",
                                data={"msg": f"frame handler crashed: {err}",
                                      "raw_hex": raw.hex(), "len": len(raw),
                                      "peer": f"{self.host}:{self.port}"}))

        self.router = FrameRouter(protocol, on_frame=on_frame, on_error=on_error)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="inspector")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self):
        backoff = self._retry
        while not self._stop.is_set():
            self._attempts += 1
            if self._retry_max_attempts and self._attempts > self._retry_max_attempts:
                return
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.bus.push(Event(ts=time.time(), kind="log", src="inspector",
                                    data={"msg": f"connected to {self.host}:{self.port}"}))
                backoff = self._retry
                try:
                    while not self._stop.is_set():
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        self.router.feed(chunk)
                finally:
                    sock.close()
                self.bus.push(Event(ts=time.time(), kind="disconnect", src="inspector",
                                    data={"peer": f"{self.host}:{self.port}", "reason": "closed"}))
            except (OSError, socket.timeout) as e:
                self.bus.push(Event(ts=time.time(), kind="log", src="inspector",
                                    data={"msg": f"connect failed: {e}; retrying in {backoff:.0f}s"}))
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, self._retry_max)
