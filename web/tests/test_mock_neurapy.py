import io
import json
import unittest
import urllib.request
from contextlib import redirect_stdout
import inspect
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


class TestMockNeurapyCompat(unittest.TestCase):
    """Lock down API compatibility with point_client's usage of neurapy.Robot.

    If point_client.py changes which kwargs / methods / attributes it accesses,
    these tests will fail and force an explicit mock update.
    """

    def test_init_accepts_socket_address_kwarg(self):
        # point_client does: Robot(socket_address=robot_ip)
        r = Robot(socket_address="127.0.0.1", sidecar_port=19970)
        self.assertIsNotNone(r)

    def test_robot_name_attribute(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19971)
        self.assertTrue(hasattr(r, "robot_name"))
        self.assertIsInstance(r.robot_name, str)

    def test_dof_attribute(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19972)
        self.assertEqual(r.dof, 6)

    def test_required_methods_exist(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=19973)
        required = [
            "get_current_joint_angles",
            "get_tcp_pose",
            "is_robot_in_teach_mode",
            "switch_to_automatic_mode",
            "power_on",
            "stop",
            "move_joint",
            "move_linear",
        ]
        for name in required:
            self.assertTrue(callable(getattr(r, name, None)), f"missing {name!r}")

    def test_move_joint_signature_accepts_point_client_kwargs(self):
        # point_client calls: move_joint(target_joint=[...], speed=..., acceleration=..., current_joint_angles=...)
        #                  and move_joint(target_pose=[...], ..., current_joint_angles=...)
        sig = inspect.signature(Robot.move_joint)
        params = sig.parameters
        for k in ("target_joint", "target_pose", "speed",
                 "acceleration", "current_joint_angles"):
            self.assertIn(k, params, f"move_joint missing param {k!r}")

    def test_move_linear_signature_accepts_point_client_kwargs(self):
        sig = inspect.signature(Robot.move_linear)
        params = sig.parameters
        for k in ("target_pose", "speed", "acceleration", "current_joint_angles"):
            self.assertIn(k, params, f"move_linear missing param {k!r}")

