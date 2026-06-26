"""Accumulate byte stream into complete protocol frames, dispatch each frame."""
from typing import Callable
from web.protocol import Protocol


class FrameRouter:
    def __init__(self, protocol: Protocol, on_frame: Callable[[bytes, str], None],
                 partial_timeout: float = 0.2):
        self._p = protocol
        self._on_frame = on_frame
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._buf.extend(data)
        n = self._p.FRAME_SIZE
        while len(self._buf) >= n:
            frame = bytes(self._buf[:n])
            del self._buf[:n]
            try:
                t = self._p.classify(frame)
            except Exception:
                t = "unknown"
            try:
                self._on_frame(frame, t)
            except Exception:
                pass  # never let consumer crash the router

    def reset(self) -> None:
        self._buf.clear()
