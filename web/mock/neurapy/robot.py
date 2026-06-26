"""Mock neurapy.Robot + sidecar HTTP for UI control.

Drop-in replacement for neurapy.robot.Robot covering the 9 methods
point_client.py actually uses (spec). Sidecar lets the UI:
  - GET  /state          -> read current state
  - POST /set_pose       -> force-teleport (test anomalies)
  - POST /reach_target   -> simulate clean arrival
  - POST /simulate_error -> raise RobotError on next move
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional


class RobotError(Exception):
    pass


class _SidecarHandler(BaseHTTPRequestHandler):
    robot: "Robot" = None

    def log_message(self, format, *args):
        pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/state":
            self._send_json(self.robot.state_dict())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            body = self._read_json()
        except Exception as e:
            self._send_json({"error": f"bad json: {e}"}, 400)
            return
        if self.path == "/set_pose":
            self.robot.teleport(joints=body.get("joints"), tcp=body.get("tcp"))
            self._send_json({"ok": True, "state": self.robot.state_dict()})
        elif self.path == "/reach_target":
            self._send_json({"ok": True, "state": self.robot.state_dict()})
        elif self.path == "/simulate_error":
            self.robot._arm_error(code=body.get("code", 1), msg=body.get("msg", ""))
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)


class Robot:
    def __init__(self, socket_address: str = "127.0.0.1", sidecar_port: int = 8766):
        self._address = socket_address
        self.dof = 6
        self._joints: List[float] = [0.0] * 6
        self._tcp: List[float] = [0.0] * 6
        self._lock = threading.Lock()
        self._pending_error: Optional[dict] = None

        _SidecarHandler.robot = self
        self._server = HTTPServer(("127.0.0.1", sidecar_port), _SidecarHandler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="mock-sidecar")
        self._thread.start()

    def state_dict(self) -> dict:
        with self._lock:
            return {"joints": list(self._joints), "tcp": list(self._tcp), "is_moving": False}

    def teleport(self, joints=None, tcp=None) -> None:
        with self._lock:
            if joints is not None: self._joints = list(joints)
            if tcp is not None: self._tcp = list(tcp)
            self._emit_state_locked()

    def _arm_error(self, code: int, msg: str) -> None:
        with self._lock:
            self._pending_error = {"code": code, "msg": msg}

    @property
    def robot_name(self) -> str:
        return "MockNeurapy"

    def get_current_joint_angles(self) -> List[float]:
        with self._lock: return list(self._joints)

    def get_tcp_pose(self) -> List[float]:
        with self._lock: return list(self._tcp)

    def is_robot_in_teach_mode(self) -> bool:
        return False

    def switch_to_automatic_mode(self) -> None:
        pass

    def power_on(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._pending_error = None
            self._emit_state_locked()

    def move_joint(self, target_joint=None, target_pose=None, speed=50.0,
                   acceleration=50.0, current_joint_angles=None) -> None:
        self._precheck()
        with self._lock:
            if target_joint is not None:
                t = target_joint[0] if isinstance(target_joint[0], list) else target_joint
                self._joints = list(t)
            self._emit_state_locked()

    def move_linear(self, target_pose=None, speed=0.25, acceleration=0.1,
                    current_joint_angles=None) -> None:
        self._precheck()
        with self._lock:
            if target_pose is not None:
                t = target_pose[0] if isinstance(target_pose[0], list) else target_pose
                self._tcp = list(t)
            self._emit_state_locked()

    def _precheck(self) -> None:
        with self._lock:
            err = self._pending_error
            self._pending_error = None
        if err is not None:
            raise RobotError(f"[{err['code']}] {err['msg']}")

    def _emit_state_locked(self) -> None:
        payload = {"event": "state", "ts": time.time(),
                   "joints": list(self._joints), "tcp": list(self._tcp), "is_moving": False}
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()
