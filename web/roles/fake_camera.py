"""TCP server that speaks the loaded protocol. Mirrors the real camera."""
import socket
import socketserver
import threading
import time
from typing import List
from web.frame_router import FrameRouter
from web.protocol import Protocol
from web.state import Event, EventBus


class FakeCamera:
    def __init__(self, protocol: Protocol, event_bus: EventBus,
                 host: str = "0.0.0.0", port: int = 9000):
        self.protocol = protocol
        self.bus = event_bus
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._clients: set = set()
        self._lock = threading.Lock()

        def on_frame(raw, t):
            # fake_camera mimics a real camera (camera→robot direction),
            # so all inbound frames are motion frames.
            try: parsed = self.protocol.parse(raw, expected_type="motion")
            except Exception as e: parsed = {"type": "unknown", "fields": {}, "error": str(e)}
            self.bus.push(Event(ts=time.time(), kind="frame_in", src="fake_camera",
                                data={"raw_hex": raw.hex(), "len": len(raw), "parsed": parsed}))

        def on_error(raw, t, err):
            self.bus.push(Event(ts=time.time(), kind="error", src="fake_camera",
                                data={"msg": f"frame handler crashed: {err}",
                                      "raw_hex": raw.hex(), "len": len(raw)}))

        self.router = FrameRouter(protocol, on_frame=on_frame, on_error=on_error)

        # Per-instance handler: binds `cam` via closure so multiple FakeCamera
        # instances don't share a class-level attribute.
        cam_ref = self
        class _Handler(socketserver.BaseRequestHandler):
            def handle(self):
                peer = f"{self.client_address[0]}:{self.client_address[1]}"
                with cam_ref._lock:
                    cam_ref._clients.add(self.request)
                cam_ref.bus.push(Event(ts=time.time(), kind="connect", src="fake_camera",
                                       data={"peer": peer, "reason": None}))
                try:
                    while True:
                        chunk = self.request.recv(4096)
                        if not chunk: break
                        cam_ref.router.feed(chunk)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
                finally:
                    with cam_ref._lock:
                        cam_ref._clients.discard(self.request)
                    cam_ref.bus.push(Event(ts=time.time(), kind="disconnect", src="fake_camera",
                                           data={"peer": peer, "reason": "client closed"}))

        self._handler_cls = _Handler

    def start(self):
        if self._server is not None: return
        self._server = socketserver.ThreadingTCPServer((self.host, self.port), self._handler_cls)
        self._server.allow_reuse_address = True  # avoid TIME_WAIT after quick restart
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="fake-camera")
        self._thread.start()

    def stop(self):
        if self._server is None: return
        self._server.shutdown(); self._server.server_close(); self._server = None

    def send_bytes(self, data: bytes) -> None:
        if self._server is None:
            raise RuntimeError("fake_camera not started")
        if len(data) != self.protocol.FRAME_SIZE:
            raise ValueError(f"data must be {self.protocol.FRAME_SIZE} bytes")
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try: c.sendall(data)
            except OSError: pass
        try: parsed = self.protocol.parse(data)
        except Exception as e: parsed = {"type": "unknown", "fields": {}, "error": str(e)}
        self.bus.push(Event(ts=time.time(), kind="frame_out", src="fake_camera",
                            data={"raw_hex": data.hex(), "len": len(data), "parsed": parsed}))

    @property
    def peer_count(self) -> int:
        with self._lock: return len(self._clients)
