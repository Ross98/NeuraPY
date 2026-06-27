"""HTTP server: REST + SSE + static files."""
import json
import mimetypes
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from web.protocol import Protocol
from web.roles.fake_camera import FakeCamera
from web.sse import sse_format
from web.state import EventBus

STATIC_DIR = Path(__file__).parent / "static"

# Hard cap on POST body size to prevent memory-exhaustion DoS from a
# client sending Content-Length: 999999999. 64 KiB is well above any
# legitimate hex payload (a 96-byte frame = 192 hex chars + JSON
# overhead ≈ 250 B) and well below any reasonable max.
MAX_BODY = 64 * 1024


class ServerState:
    """Public, mutable container shared between the HTTP handler closure
    and external callers (e.g. run.py injecting a pre-started FakeCamera).

    Previously the same data was hidden as srv._state, forcing callers to
    reach into a private attribute. Now make_server returns this directly.
    """

    def __init__(self, protocol: Protocol, bus: EventBus):
        self.protocol = protocol
        self.bus = bus
        self.fake_camera = None  # injected by run.py or /api/connect


def _to_jsonable(obj):
    """Recursively convert bytes fields (e.g. enter_area) to hex strings
    so json.dumps doesn't crash. Handles nested dicts/lists/tuples."""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, bytearray):
        return bytes(obj).hex()
    return obj


def make_server(protocol: Protocol, bus: EventBus,
                host: str = "0.0.0.0", port: int = 8765):
    """Create the HTTP server. Returns (ThreadingHTTPServer, ServerState).

    Callers that need to inject state (e.g. a pre-started FakeCamera) should
    hold onto the returned ServerState and mutate its attributes — do NOT
    poke private attributes on the server object.
    """
    state = ServerState(protocol, bus)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code, body, content_type="application/json", extra_headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(_to_jsonable(body), ensure_ascii=True).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", "0"))
            if not n:
                return {}
            if n > MAX_BODY:
                # Drain up to MAX_BODY bytes silently, then 413.
                # Without the drain, client gets ConnectionReset because
                # we respond + close before it finishes sending the body.
                try:
                    self.rfile.read(min(n, MAX_BODY))
                except Exception:
                    pass
                self._send(413, {"error": f"body too large: {n} > {MAX_BODY}"})
                return None
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                index = STATIC_DIR / "index.html"
                if not index.exists():
                    self._send(404, {"error": "index.html not built yet"})
                    return
                self._send(200, index.read_bytes(), content_type="text/html; charset=utf-8")
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
                target = (STATIC_DIR / rel).resolve()
                if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
                    self._send(403, {"error": "forbidden"})
                    return
                if not target.exists() or not target.is_file():
                    self._send(404, {"error": "not found"})
                    return
                ctype, _ = mimetypes.guess_type(str(target))
                self._send(200, target.read_bytes(), content_type=ctype or "application/octet-stream")
            elif path == "/api/snapshot":
                self._send(200, {"events": state.bus.snapshot(),
                                 "state": {}, "connections": []})
            elif path == "/api/schema":
                self._send(200, state.protocol.schema)
            elif path == "/api/stream":
                self._handle_sse()
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._read_json()
            except Exception as e:
                self._send(400, {"error": f"bad json: {e}"})
                return
            if body is None:
                # _read_json already sent an error response (413)
                return

            if path == "/api/parse":
                try:
                    raw = bytes.fromhex(body.get("hex", ""))
                except ValueError as e:
                    self._send(400, {"error": f"bad hex: {e}"})
                    return
                try:
                    parsed = state.protocol.parse(raw)
                except Exception as e:
                    self._send(400, {"error": f"parse failed: {e}"})
                    return
                self._send(200, {"type": parsed.get("type"),
                                 "fields": parsed.get("fields", {}),
                                 "raw_hex": raw.hex(), "len": len(raw)})
            elif path == "/api/build":
                ftype = body.get("type")
                fields = body.get("fields", {})
                if not ftype:
                    self._send(400, {"error": "type required"})
                    return
                try:
                    raw = state.protocol.build(ftype, **fields)
                except Exception as e:
                    self._send(400, {"error": f"build failed: {e}"})
                    return
                try:
                    # We just built it — we know the type. Pass it as a
                    # hint so motion/status disambiguation is deterministic.
                    parsed = state.protocol.parse(raw, expected_type=ftype)
                except Exception:
                    parsed = {"type": ftype, "fields": fields}
                self._send(200, {"hex": raw.hex(), "len": len(raw),
                                 "parsed": parsed, "raw_hex": raw.hex()})
            elif path == "/api/send":
                target = body.get("target", "fake_camera")
                try:
                    raw = bytes.fromhex(body.get("hex", ""))
                except ValueError as e:
                    self._send(400, {"error": f"bad hex: {e}"})
                    return
                if target != "fake_camera" or state.fake_camera is None:
                    self._send(503, {"error": f"target {target!r} not available"})
                    return
                state.fake_camera.send_bytes(raw)
                self._send(200, {"ok": True, "raw_hex": raw.hex(), "len": len(raw)})
            elif path == "/api/connect":
                role = body.get("role", "fake_camera")
                port = int(body.get("port", 9000))
                if role != "fake_camera":
                    self._send(400, {"error": f"unknown role: {role!r}"})
                    return
                if state.fake_camera is not None:
                    self._send(409, {"error": "fake_camera already running"})
                    return
                try:
                    cam = FakeCamera(state.protocol, state.bus,
                                     host="0.0.0.0", port=port)
                    cam.start()
                except OSError as e:
                    self._send(503, {"error": f"port {port} bind failed: {e}"})
                    return
                state.fake_camera = cam
                self._send(200, {"ok": True, "role": role, "port": port})
            elif path == "/api/disconnect":
                role = body.get("role", "fake_camera")
                if role != "fake_camera":
                    self._send(400, {"error": f"unknown role: {role!r}"})
                    return
                if state.fake_camera is None:
                    self._send(409, {"error": "fake_camera not running"})
                    return
                state.fake_camera.stop()
                state.fake_camera = None
                self._send(200, {"ok": True, "role": role})
            else:
                self._send(404, {"error": "not found"})

        def _handle_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = state.bus.subscribe_queue()
            try:
                while True:
                    try:
                        ev = q.get(timeout=15.0)
                    except queue.Empty:
                        try:
                            self.wfile.write(b":keepalive\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, OSError):
                            break
                        continue
                    payload = {"ts": ev.ts, "kind": ev.kind,
                               "src": ev.src, "data": ev.data}
                    try:
                        self.wfile.write(sse_format(payload))
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        break
            finally:
                state.bus.unsubscribe(q)

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv, state