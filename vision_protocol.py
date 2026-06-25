"""
VisionInspectRobot 96 字节二进制协议 (小端 LE)。

完全独立模块, 不依赖 neurapy / socket / 任何外部包, 可被:
  - point_client.py (机器人端脚本) 用于收发帧
  - parse_frame.py (离线协议调试工具) 用于解 hex 字符串
  - 第三方 / 测试代码 直接 import
"""

import struct

# ---------- 帧常量 ----------
FRAME_SIZE   = 96
HEADER_QUERY = b"\x02\x02\x02\x02"   # 软件查询帧头
ROBOT_BRAND  = 0x02                  # 占位 KUKA 编码 (Neura 不在 spec 列表里)

# 状态字段含义 (Byte52-59)
STATUS_IDLE       = 0
STATUS_RESPONDING = 1
FLAG_AT_ORIGIN    = 1
FLAG_ESTOP        = 1
FLAG_IS_MOVING    = 1
FLAG_MAIN_RUNNING = 1


class VisionProtocol:
    """VisionInspectRobot 96 字节帧的解析与构造。"""

    # ---- 查询帧识别 ----
    @staticmethod
    def is_query(frame: bytes) -> bool:
        return len(frame) >= 4 and frame[:4] == HEADER_QUERY

    # ---- 运动控制帧 (相机 -> 机器人) ----
    @staticmethod
    def parse_motion(frame: bytes) -> dict:
        assert len(frame) == FRAME_SIZE, f"frame size {len(frame)} != {FRAME_SIZE}"
        return {
            "robot_brand":    frame[0],
            "endian_flag":    frame[1],
            "function1":      frame[2],
            "function2":      frame[3],
            "joints":         list(struct.unpack("<6f", frame[4:28])),
            "position":       list(struct.unpack("<3f", frame[28:40])),
            "orientation":    list(struct.unpack("<3f", frame[40:52])),
            "work_area":      frame[52],
            "speed":          frame[53],
            "blend_radius":   frame[54],
            "motion_type":    frame[55],   # 1=MoveAbsJ 2=MoveJ 3=MoveL
            "request_motion": frame[56],
            "point_id":       struct.unpack("<i", frame[57:61])[0],
            "enter_area":     frame[61:77],
            "exit_area":      frame[77:93],
        }

    @staticmethod
    def build_motion(joints, position=(0,0,0), orientation=(0,0,0),
                     work_area=0, speed=5, blend_radius=0, motion_type=1,
                     request_motion=1, point_id=1) -> bytes:
        frame = bytearray(FRAME_SIZE)
        frame[0] = ROBOT_BRAND
        frame[1] = 0x01
        frame[2] = 0x01
        frame[3] = 0x00
        for i, j in enumerate(joints[:6]):
            struct.pack_into("<f", frame, 4 + i*4, float(j))
        for i, p in enumerate(position[:3]):
            struct.pack_into("<f", frame, 28 + i*4, float(p))
        for i, o in enumerate(orientation[:3]):
            struct.pack_into("<f", frame, 40 + i*4, float(o))
        frame[52] = work_area & 0xFF
        frame[53] = speed & 0xFF
        frame[54] = blend_radius & 0xFF
        frame[55] = motion_type & 0xFF
        frame[56] = request_motion & 0xFF
        struct.pack_into("<i", frame, 57, int(point_id))
        return bytes(frame)

    # ---- 状态帧 (机器人 -> 相机) ----
    @staticmethod
    def build_status(joints, position, orientation, *,
                     work_status=STATUS_IDLE, at_origin=0, emergency_stop=0,
                     is_moving=0, main_program_started=1,
                     work_area=0, exception=0, exception_code=0) -> bytes:
        frame = bytearray(FRAME_SIZE)
        frame[0] = ROBOT_BRAND
        frame[1] = 0x01
        frame[2] = 0x01
        frame[3] = 0x00
        for i, j in enumerate(joints[:6]):
            struct.pack_into("<f", frame, 4 + i*4, float(j))
        for i, p in enumerate(position[:3]):
            struct.pack_into("<f", frame, 28 + i*4, float(p))
        for i, o in enumerate(orientation[:3]):
            struct.pack_into("<f", frame, 40 + i*4, float(o))
        frame[52] = work_status & 0xFF
        frame[53] = at_origin & 0xFF
        frame[54] = emergency_stop & 0xFF
        frame[55] = is_moving & 0xFF
        frame[56] = main_program_started & 0xFF
        frame[57] = work_area & 0xFF
        frame[58] = exception & 0xFF
        frame[59] = exception_code & 0xFF
        return bytes(frame)

    @staticmethod
    def parse_status(frame: bytes) -> dict:
        assert len(frame) == FRAME_SIZE
        return {
            "joints":               list(struct.unpack("<6f", frame[4:28])),
            "position":             list(struct.unpack("<3f", frame[28:40])),
            "orientation":          list(struct.unpack("<3f", frame[40:52])),
            "work_status":          frame[52],
            "at_origin":            frame[53],
            "emergency_stop":       frame[54],
            "is_moving":            frame[55],
            "main_program_started": frame[56],
            "work_area":            frame[57],
            "exception":            frame[58],
            "exception_code":       frame[59],
        }
