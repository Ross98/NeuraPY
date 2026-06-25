"""
NeuraPy 相机点位 TCP 客户端 — VisionInspectRobot 二进制协议版。

按 VisionInspectRobot 通讯协议 (xlsx) 实现, 96 字节定长帧, 小端编码。
脚本运行在 Neura 机器人控制器(或同网段 PC)上, 主动 dial 相机 TCP 服务端:

  相机 -> 机器人:  查询帧    (4 字节 02 02 02 02 + 92 字节 0)
                  运动控制帧 (品牌/编码/6 关节角/末端 XYZ/RPY/运动方式/点编号 …)
  机器人 -> 相机:  状态帧    (当前 6 关节角/末端 XYZ/RPY/工作状态位 …)

运动方式 -> neurapy 映射:
  1 MoveAbsJ  -> move_joint(target_joint=[...])  关节空间绝对运动
  2 MoveJ     -> move_joint(target_pose=[...])   关节空间运动到目标位姿 (走 IK)
  3 MoveL     -> move_linear(target_pose=[...])  直线运动

单位换算(协议用 deg / mm / deg, Neura 内部用 rad / m / rad, 可关掉):
  --joint-unit {deg,rad}     默认 deg
  --position-unit {mm,m}     默认 mm
  --orientation-unit {deg,rad} 默认 deg

启动:
  python point_client.py --camera-host 192.168.2.50 --camera-port 9000
  python point_client.py --camera-host 127.0.0.1 --camera-port 9000 --auto-servo -v

部署注意: neurapy 只支持 Ubuntu 18.04/20.04 (PDF §4.2), macOS / Windows 上
跑不了。本机联调可用 test_binary.py (内联 mock neurapy)。
"""

import argparse
import logging
import math
import socket
import struct
import time

from vision_protocol import (
    FRAME_SIZE, ROBOT_BRAND, STATUS_IDLE, STATUS_RESPONDING,
    FLAG_IS_MOVING, FLAG_MAIN_RUNNING, VisionProtocol,
)
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger("point_client")

# 协议常量 + 编解码全部从 vision_protocol 导入 (避免重复定义)


# ---------- neurapy 包装 ----------
class NeuraDriver:
    def __init__(self, robot_ip: str):
        from neurapy.robot import Robot
        self._r = Robot(socket_address=robot_ip)
        LOG.info("neurapy connected to %s (name=%s dof=%s)",
                 robot_ip, self._r.robot_name, self._r.dof)

    def state(self) -> dict:
        """当前状态(Neura 单位: rad / m / rad)。"""
        return {
            "joints": list(self._r.get_current_joint_angles()),
            "tcp":    list(self._r.get_tcp_pose()),   # [X,Y,Z,R,P,Y]
        }

    def switch_auto(self):
        if self._r.is_robot_in_teach_mode():
            self._r.switch_to_automatic_mode()
    def power_on(self): self._r.power_on()
    def stop(self):      self._r.stop()

    def move_absj(self, joints_rad, speed_pct=50.0, accel_pct=50.0):
        self._r.move_joint(target_joint=[list(joints_rad)],
                           speed=float(speed_pct),
                           acceleration=float(accel_pct),
                           current_joint_angles=self._r.get_current_joint_angles())

    def movej(self, pose6, speed_pct=50.0, accel_pct=50.0):
        """MoveJ: 关节空间运动到目标位姿 (KUKA 协议, Neura 用 move_joint + target_pose + IK)。"""
        self._r.move_joint(target_pose=[list(pose6)],
                           speed=float(speed_pct),
                           acceleration=float(accel_pct),
                           current_joint_angles=self._r.get_current_joint_angles())

    def movel(self, pose6, speed=0.25, accel=0.1):
        self._r.move_linear(target_pose=[list(pose6)],
                            speed=speed, acceleration=accel,
                            current_joint_angles=self._r.get_current_joint_angles())


# ---------- 单位换算 ----------
class UnitConv:
    DEG2RAD = math.pi / 180.0
    RAD2DEG = 180.0 / math.pi
    MM2M    = 1.0 / 1000.0
    M2MM    = 1000.0

    def __init__(self, joint="deg", position="mm", orientation="deg"):
        self.joint = joint
        self.position = position
        self.orientation = orientation

    def joints_to_rad(self, j):  return [x * self.DEG2RAD for x in j] if self.joint == "deg" else list(j)
    def joints_from_rad(self, j):return [x * self.RAD2DEG for x in j] if self.joint == "deg" else list(j)
    def pos_to_m(self, p):       return [x * self.MM2M    for x in p] if self.position == "mm" else list(p)
    def pos_from_m(self, p):     return [x * self.M2MM    for x in p] if self.position == "mm" else list(p)
    def ori_to_rad(self, o):     return [x * self.DEG2RAD for x in o] if self.orientation == "deg" else list(o)
    def ori_from_rad(self, o):   return [x * self.RAD2DEG for x in o] if self.orientation == "deg" else list(o)


# ---------- 配置 ----------
@dataclass
class Config:
    camera_host: str = "192.168.2.50"
    camera_port: int = 9000
    robot_ip:    str = "192.168.2.13"
    joint_unit:      str = "deg"
    position_unit:   str = "mm"
    orientation_unit:str = "deg"
    speed_pct:       float = 50.0
    linear_speed:    float = 0.25
    log_every:       int = 1
    connect_timeout: float = 10.0
    recv_timeout:     float = 60.0   # 相机发帧间隔, 超过这个时间就当掉线重连
    reconnect_initial: float = 1.0
    reconnect_max:     float = 30.0


# ---------- 主循环 ----------
class PointClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.driver = NeuraDriver(cfg.robot_ip)
        self.u = UnitConv(cfg.joint_unit, cfg.position_unit, cfg.orientation_unit)
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._reconnect_delay = cfg.reconnect_initial
        self._is_moving = False
        self._frame_count = 0

    def run(self):
        self._running = True
        LOG.info("point_client started; will dial camera %s:%d "
                 "(joint=%s pos=%s ori=%s)",
                 self.cfg.camera_host, self.cfg.camera_port,
                 self.cfg.joint_unit, self.cfg.position_unit, self.cfg.orientation_unit)
        while self._running:
            try:
                self._dial_loop()
            except (ConnectionError, OSError, socket.timeout) as e:
                LOG.warning("disconnected: %s; retry in %.1fs", e, self._reconnect_delay)
                if not self._sleep(self._reconnect_delay):
                    break
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.cfg.reconnect_max)
            except KeyboardInterrupt:
                break
            else:
                self._reconnect_delay = self.cfg.reconnect_initial
        self._running = False
        LOG.info("point_client stopped")

    def stop(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except OSError: pass

    @staticmethod
    def _sleep(secs: float) -> bool:
        try: time.sleep(secs); return True
        except KeyboardInterrupt: return False

    # ---- 网络 ----
    def _dial_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(self.cfg.connect_timeout)   # 只管 connect 阶段
        s.connect((self.cfg.camera_host, self.cfg.camera_port))
        s.settimeout(self.cfg.recv_timeout)       # 之后 recv 用独立的超时
        self._sock = s
        LOG.info("connected to camera %s:%d",
                 self.cfg.camera_host, self.cfg.camera_port)
        self._reconnect_delay = self.cfg.reconnect_initial

        try:
            while self._running:
                frame = self._recv_exact(FRAME_SIZE)
                self._frame_count += 1
                if self.cfg.log_every and self._frame_count % self.cfg.log_every == 0:
                    LOG.info("rx frame #%d (head=%02x %02x %02x %02x)",
                             self._frame_count, frame[0], frame[1], frame[2], frame[3])
                self._handle_frame(frame)
        finally:
            self._sock = None
            try: s.close()
            except OSError: pass

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed")
            buf += chunk
        return buf

    def _send(self, data: bytes):
        if not self._sock: return
        try:
            self._sock.sendall(data)
        except OSError as e:
            LOG.warning("send failed: %s", e)

    # ---- 业务 ----
    def _handle_frame(self, frame: bytes):
        if VisionProtocol.is_query(frame):
            self._handle_query()
        else:
            self._handle_motion(frame)

    def _handle_query(self):
        try:
            st = self.driver.state()
        except Exception as e:
            LOG.warning("read state failed: %s", e)
            return
        status = VisionProtocol.build_status(
            joints=self.u.joints_from_rad(st["joints"]),
            position=self.u.pos_from_m(st["tcp"][:3]),
            orientation=self.u.ori_from_rad(st["tcp"][3:6]),
            work_status=STATUS_RESPONDING if self._is_moving else STATUS_IDLE,
            is_moving=FLAG_IS_MOVING if self._is_moving else 0,
            main_program_started=FLAG_MAIN_RUNNING,
        )
        self._send(status)
        LOG.debug("tx status: joints[0]=%.3f pos=%s",
                  self.u.joints_from_rad(st["joints"])[0], self.u.pos_from_m(st["tcp"][:3]))

    def _handle_motion(self, frame: bytes):
        m = VisionProtocol.parse_motion(frame)
        LOG.info("motion: type=%d point_id=%d request=%d speed=%d",
                 m["motion_type"], m["point_id"], m["request_motion"], m["speed"])

        # 不请求运动: 当作查询, 回送状态
        if not m["request_motion"]:
            self._handle_query()
            return

        # 单位换算: 协议单位 -> Neura 内部单位
        joints_rad = self.u.joints_to_rad(m["joints"])
        pose_xyz_m = self.u.pos_to_m(m["position"])
        pose_rpy_rad = self.u.ori_to_rad(m["orientation"])
        pose6 = pose_xyz_m + pose_rpy_rad

        # 协议 speed (0-9) -> Neura 关节速度百分比
        speed_pct = float(m["speed"]) * 10.0 if m["speed"] > 0 else self.cfg.speed_pct
        speed_pct = max(min(speed_pct, 100.0), 1.0)

        try:
            self._is_moving = True
            if m["motion_type"] == 1:
                self.driver.move_absj(joints_rad, speed_pct, speed_pct)
            elif m["motion_type"] == 2:
                self.driver.movej(pose6, speed_pct, speed_pct)
            elif m["motion_type"] == 3:
                self.driver.movel(pose6,
                                  speed=self.cfg.linear_speed,
                                  accel=self.cfg.linear_speed / 2.5)
            else:
                LOG.warning("unknown motion_type=%d", m["motion_type"])
        except Exception as e:
            LOG.exception("motion error: %s", e)
        finally:
            self._is_moving = False

        # 回送状态
        self._handle_query()


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(
        description="NeuraPy 相机点位 TCP 客户端 (VisionInspectRobot 96 字节二进制协议)")
    ap.add_argument("--camera-host", default="192.168.2.50")
    ap.add_argument("--camera-port", type=int, default=9000)
    ap.add_argument("--robot-ip", default="192.168.2.13")
    ap.add_argument("--joint-unit", choices=["deg", "rad"], default="deg",
                    help="协议关节角单位 (默认 deg, Neura 内部转 rad)")
    ap.add_argument("--position-unit", choices=["mm", "m"], default="mm",
                    help="协议位置单位 (默认 mm, Neura 内部转 m)")
    ap.add_argument("--orientation-unit", choices=["deg", "rad"], default="deg",
                    help="协议姿态单位 (默认 deg, Neura 内部转 rad)")
    ap.add_argument("--speed-pct", type=float, default=50.0)
    ap.add_argument("--linear-speed", type=float, default=0.25)
    ap.add_argument("--log-every", type=int, default=1)
    ap.add_argument("--connect-timeout", type=float, default=10.0,
                    help="dial 相机时 TCP 握手超时 (秒), 默认 10")
    ap.add_argument("--recv-timeout", type=float, default=60.0,
                    help="等待相机下一帧超时 (秒), 默认 60, 设很大就接近阻塞")
    ap.add_argument("--reconnect-initial", type=float, default=1.0)
    ap.add_argument("--reconnect-max", type=float, default=30.0)
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    cfg = Config(
        camera_host=args.camera_host, camera_port=args.camera_port,
        robot_ip=args.robot_ip,
        joint_unit=args.joint_unit, position_unit=args.position_unit,
        orientation_unit=args.orientation_unit,
        speed_pct=args.speed_pct, linear_speed=args.linear_speed,
        log_every=args.log_every, connect_timeout=args.connect_timeout, recv_timeout=args.recv_timeout,
        reconnect_initial=args.reconnect_initial,
        reconnect_max=args.reconnect_max,
    )
    PointClient(cfg).run()


if __name__ == "__main__":
    main()
