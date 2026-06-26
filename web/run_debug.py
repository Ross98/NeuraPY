#!/usr/bin/env python3
"""Full closed-loop debug UI: fake_camera + point_client subprocess (mock neurapy
via PYTHONPATH) + web server. Designed for dev machines without real neurapy.

point_client does `from neurapy.robot import Robot` (never modified). We
prepend web/mock/ to PYTHONPATH so the mock satisfies that import.
point_client stdout JSON `state` events get pushed into the EventBus.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# Allow running as `python web/run_debug.py` from the project root:
# add the project root to sys.path so `import web.protocols` resolves.
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)
from pathlib import Path

from web.protocols import load
from web.roles.fake_camera import FakeCamera
from web.server import make_server
from web.state import Event, EventBus

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
MOCK_PKG_PARENT = HERE / "mock"


def main():
    ap = argparse.ArgumentParser(description="Full closed-loop debug UI")
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fake-camera-port", type=int, default=9000)
    ap.add_argument("--mock-sidecar-port", type=int, default=8766)
    ap.add_argument("--point-client-args", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    try:
        protocol = load(args.protocol)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    bus = EventBus()
    cam = FakeCamera(protocol, bus, host="0.0.0.0", port=args.fake_camera_port)
    cam.start()
    print(f"fake_camera on :{args.fake_camera_port}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(MOCK_PKG_PARENT) + os.pathsep + env.get("PYTHONPATH", "")
    env["MOCK_SIDECAR_PORT"] = str(args.mock_sidecar_port)

    pc_args = [sys.executable, str(PROJECT_ROOT / "point_client.py"),
               "--camera-host", "127.0.0.1",
               "--camera-port", str(args.fake_camera_port)]
    pc_args += args.point_client_args

    print(f"starting: {' '.join(pc_args)}  PYTHONPATH+={MOCK_PKG_PARENT}")
    proc = subprocess.Popen(pc_args, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=1, text=True)

    def drain_stdout():
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    if obj.get("event") == "state":
                        bus.push(Event(
                            ts=obj.get("ts", time.time()),
                            kind="state", src="mock",
                            data={"joints_rad": obj.get("joints", []),
                                  "tcp": obj.get("tcp", []),
                                  "is_moving": obj.get("is_moving", False)}))
                        continue
                except json.JSONDecodeError:
                    pass
            bus.push(Event(ts=time.time(), kind="log", src="point_client",
                           data={"msg": line}))

    threading.Thread(target=drain_stdout, daemon=True,
                     name="point-client-stdout").start()

    shutdown = threading.Event()

    def watch_exit():
        rc = proc.wait()
        bus.push(Event(ts=time.time(), kind="log", src="run_debug",
                       data={"msg": f"point_client exited {rc}; restarting in 2s"}))
        time.sleep(2.0)
        if not shutdown.is_set():
            main()

    threading.Thread(target=watch_exit, daemon=True, name="watch-exit").start()

    srv = make_server(protocol, bus, host=args.host, port=args.port)
    srv._state.fake_camera = cam
    print(f"web UI on http://{args.host}:{args.port}  protocol={protocol.__class__.__name__}")
    print("press Ctrl-C to exit")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown.set()
        try:
            proc.terminate()
        except Exception:
            pass
        cam.stop()
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
