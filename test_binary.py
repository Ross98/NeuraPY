"""单元 + 端到端测试, 用于 VisionInspectRobot 二进制协议实现。

跑法: python test_binary.py

设计目的: 抓两类 bug
  1) point_id 字段字节序错误 (spec 写 BE, 样例 06 00 00 00 -> 6; 用 LE 解 -> 100663296)
  2) MoveJ 应当用 target_pose (走 IK), 不是 target_joint
"""

import math, socket, struct, sys, threading, time, types

# mock neurapy
fake = types.ModuleType("neurapy")
fake_robot_mod = types.ModuleType("neurapy.robot")
class FakeRobot:
    def __init__(self, **kw):
        self.robot_name = "L"; self.dof = 6; self.calls = []
        self._joints = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        self._tcp = [0.5, 0.1, 0.4, 3.14, 0.0, 0.0]
    def get_current_joint_angles(self): return list(self._joints)
    def get_tcp_pose(self): return list(self._tcp)
    def move_joint(self, **kw):
        self.calls.append(("move_joint", kw)); return True
    def move_linear(self, **kw):
        self.calls.append(("move_linear", kw)); return True
fake_robot_mod.Robot = FakeRobot
fake.robot = fake_robot_mod
sys.modules["neurapy"] = fake
sys.modules["neurapy.robot"] = fake_robot_mod

sys.path.insert(0, "/Users/adam/Documents/Codex/neurapy_socket_bridge")
import point_client
from point_client import VisionProtocol

fails = []
def check(cond, msg):
    print(("ok  " if cond else "FAIL") + " " + msg)
    if not cond: fails.append(msg)

# ---------- 1) xlsx 样例 ----------
def test_xlsx_sample():
    sample = bytes.fromhex(
        "02010100"
        "82C45D42A7D390C27B0E1543CB2D2542E1B6B1C2"
        "3FF526C2F6B80D44B89DAEC34BCAEA43"
        "64EC9342B65D604236247C42"
        "00" "01" "01" "01" "01"
        "06000000"
    )
    sample += b"\x00" * (96 - len(sample))
    m = VisionProtocol.parse_motion(sample)
    check(abs(m["joints"][0] - 55.44) < 0.1,  f"xlsx joint1={m['joints'][0]:.2f} (~55.44)")
    check(abs(m["position"][0] - 566.89) < 1,   f"xlsx X={m['position'][0]:.1f}mm (~566.89)")
    check(abs(m["orientation"][0] - 73.96) < 0.1, f"xlsx RX={m['orientation'][0]:.2f}deg (~73.96)")
    check(m["motion_type"] == 1,               f"xlsx motion_type={m['motion_type']} (1=MoveAbsJ)")
    check(m["point_id"] == 6,                  f"xlsx point_id={m['point_id']} (expect 6, BE int32)")

# ---------- 2) MoveJ 走 target_pose ----------
def test_movej_uses_pose():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 19910)); srv.listen(1); srv.settimeout(3.0)
    def cam():
        c, _ = srv.accept()
        c.sendall(VisionProtocol.build_motion(
            joints=[30, -20, 40, 0, 50, 0],
            position=(500.0, 100.0, 400.0), orientation=(3.14, 0, 0),
            motion_type=2, request_motion=1, point_id=99, speed=5))
        c.recv(96); c.close(); srv.close()
    threading.Thread(target=cam, daemon=True).start(); time.sleep(0.2)
    cfg = point_client.Config(camera_host="127.0.0.1", camera_port=19910,
        robot_ip="x", joint_unit="deg", position_unit="mm", orientation_unit="deg",
        reconnect_initial=0.5, reconnect_max=2.0)
    c = point_client.PointClient(cfg)
    threading.Thread(target=c.run, daemon=True).start()
    time.sleep(0.7); c.stop(); time.sleep(0.3)
    calls = [x for x in c.driver._r.calls if x[0] == "move_joint"]
    check(len(calls) == 1, f"MoveJ -> move_joint x{len(calls)}")
    if calls:
        kw = calls[0][1]
        check("target_pose" in kw,   f"MoveJ 用 target_pose (keys={list(kw.keys())})")
        check("target_joint" not in kw, f"MoveJ 不该用 target_joint")
        if "target_pose" in kw:
            p = kw["target_pose"][0]
            check(abs(p[0]-0.5)<0.01 and abs(p[1]-0.1)<0.01 and abs(p[2]-0.4)<0.01,
                  f"MoveJ pose={p} (expect [0.5,0.1,0.4,...])")

# ---------- 3) MoveAbsJ 走 target_joint ----------
def test_moveabsj_uses_joint():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 19911)); srv.listen(1); srv.settimeout(3.0)
    def cam():
        c, _ = srv.accept()
        c.sendall(VisionProtocol.build_motion(
            joints=[30, -20, 40, 0, 50, 0],
            motion_type=1, request_motion=1, point_id=2, speed=5))
        c.recv(96); c.close(); srv.close()
    threading.Thread(target=cam, daemon=True).start(); time.sleep(0.2)
    cfg = point_client.Config(camera_host="127.0.0.1", camera_port=19911,
        robot_ip="x", joint_unit="deg", position_unit="mm", orientation_unit="deg",
        reconnect_initial=0.5, reconnect_max=2.0)
    c = point_client.PointClient(cfg)
    threading.Thread(target=c.run, daemon=True).start()
    time.sleep(0.7); c.stop(); time.sleep(0.3)
    calls = [x for x in c.driver._r.calls if x[0] == "move_joint"]
    check(len(calls) == 1, f"MoveAbsJ -> move_joint x{len(calls)}")
    if calls:
        kw = calls[0][1]
        check("target_joint" in kw, f"MoveAbsJ 用 target_joint (keys={list(kw.keys())})")
        if "target_joint" in kw:
            j = kw["target_joint"][0]
            check(abs(j[0]-math.radians(30))<0.01, f"MoveAbsJ j1={math.degrees(j[0]):.2f}deg (30)")

# ---------- 4) MoveL 走 target_pose + move_linear ----------
def test_movel_uses_pose():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 19912)); srv.listen(1); srv.settimeout(3.0)
    def cam():
        c, _ = srv.accept()
        c.sendall(VisionProtocol.build_motion(
            joints=[30, -20, 40, 0, 50, 0],
            position=(500.0, 100.0, 400.0), orientation=(3.14, 0, 3.14),
            motion_type=3, request_motion=1, point_id=4, speed=5))
        c.recv(96); c.close(); srv.close()
    threading.Thread(target=cam, daemon=True).start(); time.sleep(0.2)
    cfg = point_client.Config(camera_host="127.0.0.1", camera_port=19912,
        robot_ip="x", joint_unit="deg", position_unit="mm", orientation_unit="deg",
        reconnect_initial=0.5, reconnect_max=2.0)
    c = point_client.PointClient(cfg)
    threading.Thread(target=c.run, daemon=True).start()
    time.sleep(0.7); c.stop(); time.sleep(0.3)
    calls = [x for x in c.driver._r.calls if x[0] == "move_linear"]
    check(len(calls) == 1, f"MoveL -> move_linear x{len(calls)}")
    if calls:
        kw = calls[0][1]
        check("target_pose" in kw, f"MoveL 用 target_pose (keys={list(kw.keys())})")
        if "target_pose" in kw:
            p = kw["target_pose"][0]
            check(abs(p[0]-0.5)<0.01 and abs(p[2]-0.4)<0.01, f"MoveL pose={p}")

# ---------- 5) 查询响应格式 ----------
def test_query_response():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 19913)); srv.listen(1); srv.settimeout(3.0)
    captured = []
    def cam():
        c, _ = srv.accept()
        c.sendall(b"\x02\x02\x02\x02" + b"\x00" * 92)
        captured.append(c.recv(96))
        c.close(); srv.close()
    threading.Thread(target=cam, daemon=True).start(); time.sleep(0.2)
    cfg = point_client.Config(camera_host="127.0.0.1", camera_port=19913,
        robot_ip="x", joint_unit="deg", position_unit="mm", orientation_unit="deg")
    c = point_client.PointClient(cfg)
    threading.Thread(target=c.run, daemon=True).start()
    time.sleep(0.5); c.stop(); time.sleep(0.3)
    check(len(captured) == 1, f"query got 1 response (got {len(captured)})")
    if captured:
        st = VisionProtocol.parse_status(captured[0])
        check(abs(st["joints"][0] - 5.73) < 0.2, f"resp joint1={st['joints'][0]:.2f}deg (~5.73, fake 0.1rad)")
        check(abs(st["position"][0] - 500) < 1,  f"resp X={st['position'][0]:.1f}mm (~500)")

if __name__ == "__main__":
    test_xlsx_sample()
    test_movej_uses_pose()
    test_moveabsj_uses_joint()
    test_movel_uses_pose()
    test_query_response()
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        for m in fails: print(" -", m)
        sys.exit(1)
    print("ALL OK")
