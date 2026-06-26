import io
import json
import unittest
import urllib.request
from contextlib import redirect_stdout
from web.mock.neurapy import Robot, RobotError


def _sidecar(port):
    class _S:
        def get(self, path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as r:
                return json.loads(r.read().decode())
        def post(self, path, body):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1) as r:
                return json.loads(r.read().decode())
    return _S()


class TestMockNeurapy(unittest.TestCase):
    def test_init_creates_state(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19766)
        self.assertEqual(r.dof, 6)
        self.assertEqual(len(r.get_current_joint_angles()), 6)
        self.assertEqual(len(r.get_tcp_pose()), 6)

    def test_move_joint_changes_state(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19767)
        r.move_joint(target_joint=[[10, 20, 30, 0, 40, 0]], speed=50.0,
                     acceleration=50.0, current_joint_angles=[0]*6)
        self.assertEqual(r.get_current_joint_angles(), [10, 20, 30, 0, 40, 0])

    def test_sidecar_get_state(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19768)
        sc = _sidecar(19768)
        st = sc.get("/state")
        self.assertIn("joints", st)
        self.assertIn("tcp", st)

    def test_sidecar_set_pose(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19769)
        sc = _sidecar(19769)
        sc.post("/set_pose", {"joints": [1, 2, 3, 4, 5, 6]})
        self.assertEqual(r.get_current_joint_angles(), [1, 2, 3, 4, 5, 6])

    def test_sidecar_simulate_error(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19770)
        sc = _sidecar(19770)
        sc.post("/simulate_error", {"code": 7, "msg": "IK fail"})
        with self.assertRaises(RobotError):
            r.move_joint(target_joint=[[0]*6], speed=50.0, acceleration=50.0,
                         current_joint_angles=[0]*6)

    def test_stdout_state_event(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19771)
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.move_joint(target_joint=[[1, 1, 1, 0, 0, 0]], speed=50.0,
                         acceleration=50.0, current_joint_angles=[0]*6)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("{")]
        self.assertTrue(any('"event": "state"' in l for l in lines), lines)
