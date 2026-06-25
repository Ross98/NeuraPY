"""
build_frame.py — 根据十进制关节角 / TCP 位姿生成 96 字节测试报文。

输出: hex + hexdump, 可:
  - 管道给 parse_frame.py 验证
  - 用 nc / 脚本发到相机
  - 用 --send-to 直接 TCP 发送 + 打印应答

只依赖 vision_protocol.py (纯 stdlib), macOS / Linux 都能跑。
"""

import argparse
import socket
import sys
from typing import Optional

from vision_protocol import VisionProtocol, FRAME_SIZE
def parse_float_list(s, n=None):
    """把 '10 -20 30' / '10,-20,30' / '[10, -20, 30]' 都解成 [10.0, -20.0, 30.0]。

    n: 严格要求数量 (None = 不限制)
    """
    if isinstance(s, (list, tuple)):
        vals = [float(x) for x in s]
    else:
        s = str(s).strip()
        # 去 [ ] (Python 列表字面量)
        if s.startswith('[') and s.endswith(']'):
            s = s[1:-1]
        # 逗号 / 空白都作为分隔符
        s = s.replace(',', ' ')
        parts = s.split()
        if not parts:
            raise argparse.ArgumentTypeError("empty list")
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid float in {s!r}")
    if n is not None and len(vals) != n:
        raise argparse.ArgumentTypeError(f"expected {n} values, got {len(vals)}: {vals}")
    return vals


def six_floats(s):
    return parse_float_list(s, n=6)


def three_floats(s):
    return parse_float_list(s, n=3)



def build_motion_frame(joints, xyz, rpy=(0, 0, 0), *,
                       motion_type=1, point_id=1, speed=5, request_motion=1,
                       work_area=0, blend_radius=0) -> bytes:
    """根据十进制关节角(度) + 位置(mm) + 姿态(度) 生成 96 字节运动控制帧。"""
    return VisionProtocol.build_motion(
        joints=list(joints),
        position=tuple(xyz),
        orientation=tuple(rpy),
        motion_type=motion_type,
        point_id=point_id,
        speed=speed,
        request_motion=request_motion,
        work_area=work_area,
        blend_radius=blend_radius,
    )


def build_query_frame() -> bytes:
    """生成 96 字节查询帧 (02 02 02 02 + 92 字节 0)。"""
    return b"\x02\x02\x02\x02" + b"\x00" * (FRAME_SIZE - 4)


def format_hexdump(frame: bytes) -> str:
    """格式化为 hexdump (16 字节一行, 带偏移)。"""
    lines = []
    for i in range(0, len(frame), 16):
        chunk = frame[i:i+16]
        lines.append(f"  [{i:3d}-{min(i+15, len(frame)-1):3d}] {chunk.hex()}")
    return "\n".join(lines)


def send_and_recv(target: str, frame: bytes, timeout: float = 5.0) -> Optional[bytes]:
    """TCP 发送一帧 + 收 96 字节应答。"""
    host, _, port = target.partition(":")
    if not port:
        print("error: --send-to 格式 host:port", file=sys.stderr)
        return None
    port = int(port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.sendall(frame)
        resp = b""
        while len(resp) < FRAME_SIZE:
            chunk = s.recv(FRAME_SIZE - len(resp))
            if not chunk:
                break
            resp += chunk
        return resp if len(resp) == FRAME_SIZE else None
    except (socket.timeout, OSError) as e:
        print(f"network error: {e}", file=sys.stderr)
        return None
    finally:
        try: s.close()
        except OSError: pass


_MOTION_NAME = {1: "MoveAbsJ", 2: "MoveJ", 3: "MoveL"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="根据十进制关节角/位姿生成 96 字节 VisionInspectRobot 测试报文",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # MoveAbsJ 到一个点
  build_frame.py --joints 0 0 0 0 0 0 --xyz 0 0 0 --point-id 1

  # MoveL 到指定位姿
  build_frame.py --joints 10 20 30 0 40 0 --xyz 500 100 300 --rpy 3.14 0 0 \\
                 --motion-type 3 --point-id 5 --speed 8

  # 生成查询帧
  build_frame.py --type query

  # 生成 + 立即发到相机, 打印应答
  build_frame.py --type query --send-to 192.168.2.99:9000

  # 管道到 parse_frame.py 验证
  build_frame.py --joints 0 0 0 0 0 0 --xyz 0 0 0 | xargs parse_frame.py
""")
    ap.add_argument("--type", choices=["motion", "query"], default="motion",
                    help="帧类型 (默认 motion)")
    ap.add_argument("--joints", type=six_floats, metavar="VALS",
                    help="6 个关节角 (度), motion 必填. 接受 3 种格式 (加引号防 zsh glob):\n                         \"10 -20 30 0 40 0\" / \"10,-20,30,0,40,0\" / \"[10, -20, 30, 0, 40, 0]\"")
    ap.add_argument("--xyz", type=three_floats, metavar="VALS",
                    help="末端位置 X Y Z (mm), motion 必填. 接受格式同 --joints")
    ap.add_argument("--rpy", type=three_floats, metavar="VALS",
                    default=[0.0, 0.0, 0.0],
                    help="末端姿态 RX RY RZ (度), 默认 [0, 0, 0]. 接受格式同 --joints")
    ap.add_argument("--motion-type", type=int, choices=[1, 2, 3], default=1,
                    help="运动方式: 1=MoveAbsJ, 2=MoveJ, 3=MoveL (默认 1)")
    ap.add_argument("--point-id", type=int, default=1,
                    help="目标点位编号 (默认 1=Home)")
    ap.add_argument("--speed", type=int, choices=range(0, 10), default=5,
                    help="速度档 0-9 (默认 5, 0=默认)")
    ap.add_argument("--blend-radius", type=int, choices=range(0, 10), default=0,
                    help="过渡半径 0-9 (默认 0)")
    ap.add_argument("--work-area", type=int, default=0, help="工作区域 (默认 0)")
    ap.add_argument("--no-execute", action="store_true",
                    help="request_motion=0, 当作查询 (不回 0 帧内不发运动)")
    ap.add_argument("--send-to", metavar="HOST:PORT", default=None,
                    help="发送到这个 TCP server 并打印 hex 应答")
    ap.add_argument("--out", choices=["hex", "hexdump", "both"], default="hexdump",
                    help="输出格式 (默认 hexdump, hex 是单行方便管道)")
    args = ap.parse_args()

    if args.type == "query":
        frame = build_query_frame()
        desc = "查询帧"
    else:
        if not args.joints or not args.xyz:
            ap.error("motion 帧需要 --joints 和 --xyz")
        frame = build_motion_frame(
            joints=args.joints, xyz=args.xyz, rpy=args.rpy,
            motion_type=args.motion_type,
            point_id=args.point_id,
            speed=args.speed,
            blend_radius=args.blend_radius,
            work_area=args.work_area,
            request_motion=0 if args.no_execute else 1,
        )
        name = _MOTION_NAME.get(args.motion_type, "?")
        desc = (f"运动控制帧: {name} (type={args.motion_type}) "
                f"point_id={args.point_id} speed={args.speed} "
                f"request_motion={0 if args.no_execute else 1}")

    print(f">>> {desc}")
    print(f">>> frame ({len(frame)} bytes):")
    print(format_hexdump(frame))
    print()

    if args.out in ("hex", "both"):
        print("hex (一行, 可管道给 parse_frame.py / nc):")
        print(frame.hex())
        print()

    if args.send_to:
        print(f">>> 发送到 {args.send_to} ...")
        resp = send_and_recv(args.send_to, frame)
        if resp is None:
            print("发送或接收失败 (超时/对端关闭)")
            return 1
        print(f">>> 应答 ({len(resp)} bytes):")
        print(format_hexdump(resp))
        print()
        print("应答 hex:", resp.hex())
    return 0


if __name__ == "__main__":
    sys.exit(main())
