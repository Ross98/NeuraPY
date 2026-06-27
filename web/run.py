#!/usr/bin/env python3
"""Web UI entry point. Loads a Protocol and starts the HTTP server.

Examples:
    python web/run.py --protocol neurapy --auto-start-camera
    python web/run.py --protocol ./my_proto.py:MyProto --port 9001
    python web/run.py --protocol neurapy --inspector-connect 192.168.2.50:9000
"""
import os
import sys

# Allow running as `python web/run.py` from the project root:
# add the project root to sys.path so `import web.protocols` resolves.
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import argparse

from web.protocols import load
from web.roles.fake_camera import FakeCamera
from web.roles.inspector_client import InspectorClient
from web.server import make_server
from web.state import EventBus


def main():
    ap = argparse.ArgumentParser(description="NeuraPY-style debug UI")
    ap.add_argument("--protocol", help="Protocol name (registry) or 'path/to/file.py:Class'")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fake-camera-port", type=int, default=9000)
    ap.add_argument("--inspector-connect", help="host:port to passively capture frames from")
    ap.add_argument("--auto-start-camera", action="store_true",
                    help="start fake_camera automatically")
    args = ap.parse_args()

    try:
        protocol = load(args.protocol)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    bus = EventBus()
    cam = ic = None
    if args.auto_start_camera:
        cam = FakeCamera(protocol, bus, host="0.0.0.0", port=args.fake_camera_port)
        cam.start()
        print(f"fake_camera listening on :{args.fake_camera_port}")
    if args.inspector_connect:
        host, _, p = args.inspector_connect.partition(":")
        ic = InspectorClient(protocol, bus, host=host, port=int(p))
        ic.start()
        print(f"inspector connected to {host}:{p}")

    srv, state = make_server(protocol, bus, host=args.host, port=args.port)
    state.fake_camera = cam
    print(f"web UI on http://{args.host}:{args.port}  protocol={protocol.__class__.__name__}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        if cam:
            cam.stop()
        if ic:
            ic.stop()
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
