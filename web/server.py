"""HTTP server: REST + SSE + static files."""
import json
import mimetypes
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from web.protocol import Protocol
from web.sse import sse_format
from web.state import EventBus

STATIC_DIR = Path(__file__).parent / "static"


class _State:
    def __init__(self, protocol: Protocol, bus: EventBus):
        self.protocol = protocol
        self.bus = bus
        self.fake_camera = None  # injected by run.py after start


def make_server(protocol: Protocol, bus: EventBus, host: str = "0.0.0.0", port: int = 8765):
    state = _State(protocol, bus)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code, body, content_type="application/json", extra_headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=True).encode("utf-8")
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
                    parsed = state.protocol.parse(raw)
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
            else:
                self._send(404, {"error": "not found"})

        def _handle_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q: queue.Queue = queue.Queue(maxsize=2000)
            state.bus._subs.append(q)
            try:
                while True:
                    try:
                        ev = q.get(timeout=15.0)
                    except queue.Empty:
                        try:
                            self.wfile.write(b": keepalive\n\n")
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
                try:
                    state.bus._subs.remove(q)
                except ValueError:
                    pass

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv._state = state  # exposed for run.py to inject fake_camera later
    return srv
