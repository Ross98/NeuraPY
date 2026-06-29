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
                 recv_timeout: float = 60.0,
                 retry_max_attempts: int = 0):
        """Passive TCP frame capturer.

        - retry_initial/recv_timeout default to point_client's defaults (1s
          backoff, 60s recv) so the inspector can sit next to a real camera
          without disconnecting during normal frame gaps.
        - connect_timeout (separate from recv_timeout) caps how long we wait
          for the SYN-ACK before giving up on this attempt.
        """
        self.protocol = protocol
        self.bus = event_bus
        self.host = host
        self.port = port
        self._retry = retry_initial
        self._retry_max = retry_max
        self._recv_timeout = recv_timeout
        self._connect_timeout = min(5.0, retry_initial)
        self._retry_max_attempts = retry_max_attempts
        self._stop = threading.Event()
        self._thread = None
        self._attempts = 0

        def on_frame(raw, t):
            try:
                parsed = self.protocol.parse(raw)
            except Exception as e:
                parsed = {"type": "unknown", "fields": {}, "error": str(e)}
            self.bus.push(Event(ts=time.time(), kind="frame_in", src="inspector",
                                data={"raw_hex": raw.hex(), "len": len(raw),
                                      "parsed": parsed,
                                      "peer": f"{self.host}:{self.port}"}))
        self.router = FrameRouter(protocol, on_frame=on_frame)

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
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self._connect_timeout)
                sock.settimeout(self._recv_timeout)
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
                                    data={"msg": f"recv failed (timeout={self._recv_timeout:.0f}s): {e}; retrying in {backoff:.0f}s"}))
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, self._retry_max)
