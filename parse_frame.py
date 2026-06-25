"""
VisionInspectRobot 96 字节协议 — 离线 hex 解析工具。

不连机器人, 不需要 neurapy, 纯协议调试 / 数据分析用。

用法:
  # 命令行参数
  python parse_frame.py "02010100 22 01 ab c1 ..."

  # 从 stdin 读 (适合管道 / 重定向)
  echo "02010100 22 01 ab c1 ..." | python parse_frame.py
  python parse_frame.py < frame.hex
  cat frames.txt | python parse_frame.py

  # 没参数也没 stdin -> 显示 usage

自动判断:
  - 02 02 02 02 头      -> 查询帧 (无数据字段)
  - 02 01 01 00 头      -> 同时解出 运动控制 + 状态 两种解释
                           (因为两种帧共用这个头, 区别在后段字段语义)
  - 其他                -> 当作 96 字节直接尝试两种解释, 失败会提示
"""

import argparse
import sys

from vision_protocol import VisionProtocol, FRAME_SIZE


def _print_dict(d: dict, indent: str = "  ") -> None:
    for k, v in d.items():
        if isinstance(v, list):
            if v and isinstance(v[0], float):
                vals = ", ".join(f"{x:.1f}" for x in v)
                print(f"{indent}{k:20s} = [{vals}]")
            elif v and isinstance(v[0], int) and len(v) > 4:
                head = v[:8].hex()
                more = " ..." if len(v) > 8 else ""
                print(f"{indent}{k:20s} = (len={len(v)}) {head}{more}")
            else:
                print(f"{indent}{k:20s} = {v}")
        else:
            print(f"{indent}{k:20s} = {v}")


def parse_hex(hex_str: str) -> int:
    """解析一段 16 进制字符串, 返回 exit code (0=ok, 1=bad input)。"""
    s = hex_str.replace(" ", "").replace("\n", "").replace("\t", "")
    try:
        data = bytes.fromhex(s)
    except ValueError as e:
        print(f"bad hex: {e}", file=sys.stderr)
        return 1

    if len(data) > FRAME_SIZE:
        print(f"warning: {len(data)} bytes, truncating to {FRAME_SIZE}", file=sys.stderr)
        data = data[:FRAME_SIZE]
    if len(data) < FRAME_SIZE:
        print(f"warning: {len(data)} bytes, padding to {FRAME_SIZE} with zeros",
              file=sys.stderr)
        data = data + b"\x00" * (FRAME_SIZE - len(data))

    print(f"frame ({len(data)} bytes), hex dump:")
    for i in range(0, FRAME_SIZE, 16):
        chunk = data[i:i+16]
        print(f"  [{i:3d}-{i+15:3d}] {chunk.hex()}")
    print()

    if VisionProtocol.is_query(data):
        print(">>> 帧类型: 查询 (HEADER_QUERY 02 02 02 02)")
        print("    查询帧没有数据字段, 收到后直接回一个 build_status() 即可。")
        return 0

    print(">>> 帧头: 02 01 01 00 (可能是运动控制 或 状态, 两边都解出来)")
    print()
    print("--- 作为 运动控制帧 (parse_motion, 相机 -> 机器人) ---")
    try:
        m = VisionProtocol.parse_motion(data)
        _print_dict(m)
    except AssertionError as e:
        print(f"  (跳过: {e})")
    print()
    print("--- 作为 状态帧 (parse_status, 机器人 -> 相机) ---")
    try:
        st = VisionProtocol.parse_status(data)
        _print_dict(st)
    except AssertionError as e:
        print(f"  (跳过: {e})")
    print()
    print("(根据上下文判断: 你正在贴的是 相机发来的指令 还是 机器人回给你的状态)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="解析 96 字节 VisionInspectRobot 协议 hex (纯离线, 不连机器人)")
    ap.add_argument("hex", nargs="?",
                    help="hex 字符串 (空格/换行可加); 不传则从 stdin 读")
    args = ap.parse_args()

    if args.hex:
        return parse_hex(args.hex)

    # 从 stdin 读
    if not sys.stdin.isatty():
        return parse_hex(sys.stdin.read())

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
