"""Accumulate byte stream into complete protocol frames, dispatch each frame."""

import time
from typing import Callable, Optional

from web.protocol import Protocol


class FrameRouter:
    """Buffers incoming bytes and emits one callback per complete frame.

    Args:
        protocol: provides FRAME_SIZE + classify(frame).
        on_frame: called with (raw_bytes, type_str) per complete frame.
        on_error: optional, called with (raw_bytes, type_str, exception)
                  if on_frame raises. Defaults to silent (matches old behavior).
        partial_timeout: seconds. If a partial buffer sits unfed for longer
                         than this, callers should call flush_partial(now=...)
                         to drop it. Prevents stale bytes from corrupting
                         the next session on a reused connection.
    """

    def __init__(self, protocol: Protocol,
                 on_frame: Callable[[bytes, str], None],
                 on_error: Optional[Callable[[bytes, str, Exception], None]] = None,
                 partial_timeout: float = 0.2):
        self._p = protocol
        self._on_frame = on_frame
        self._on_error = on_error
        self._partial_timeout = partial_timeout
        self._buf = bytearray()
        self._last_feed = 0.0

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._buf.extend(data)
        self._last_feed = time.monotonic()
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
            except Exception as e:
                if self._on_error is not None:
                    try:
                        self._on_error(frame, t, e)
                    except Exception:
                        pass  # never let error-handler crash the router

    def flush_partial(self, now: Optional[float] = None) -> int:
        """Drop buffer if it has been idle longer than partial_timeout.

        Returns number of bytes discarded. Caller-driven (no internal timer)
        so we don't introduce threading complexity into the router.
        """
        if not self._buf:
            return 0
        if now is None:
            now = time.monotonic()
        if now - self._last_feed > self._partial_timeout:
            n = len(self._buf)
            self._buf.clear()
            return n
        return 0

    def reset(self) -> None:
        self._buf.clear()