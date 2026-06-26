# NeuraPy 相机点位 TCP 客户端 — Robot 端

按 `VisionInspectRobot 通讯协议.xlsx` 实现的 96 字节定长小端二进制帧。**只关注 robot 端**: 主动 dial 相机 TCP 服务端, 解析查询/运动控制帧, 调 `neurapy.Robot` 执行, 回送状态帧。

```
┌───────────── 机器人控制器 (Ubuntu 18.04/20.04) ─────────────┐
│                                                              │
│   point_client.py                                            │
│   ├── vision_protocol  (96B 帧编解码)                         │
│   ├── neurapy.Robot  ── TCP ──►  机器人控制器 (192.168.2.13) │
│   └── socket  ──── TCP ────►  相机 (192.168.2.50:9000)        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## 1. 文件

```
neurapy_socket_bridge/
├── point_client.py     # 跑在控制器: neurapy + TCP + 协议
├── vision_protocol.py  # 96 字节协议编解码 (无外部依赖, 纯 stdlib)
├── parse_frame.py      # 离线 hex 解析工具 (macOS 上也能跑)
├── build_frame.py      # 根据关节角/位姿生成测试报文, 可直接 TCP 发给相机
├── test_binary.py      # 回归测试 (内联 mock neurapy)
└── README.md
```

模块依赖关系:
```
point_client.py ─┬─→ vision_protocol.py  (无依赖, 纯 stdlib)
                 └─→ neurapy             (Linux 才有)

parse_frame.py ────→ vision_protocol.py  (无依赖, 纯 stdlib)
build_frame.py ────→ vision_protocol.py  (无依赖, 纯 stdlib)

test_binary.py ────→ vision_protocol.py  (走 inline mock neurapy)
```

`parse_frame.py` / `build_frame.py` / `test_binary.py` **macOS 都能直接跑** (只要装了 Python 3.7+), 不需要 neurapy。

## 2. 帧结构 (96 字节, 小端 LE)

> 注: xlsx 文档里"编码格式"标 `1=大端`,但全部样例字节(浮点和 int32)用小端解都对得上(55.4° / 567mm / 6),用大端解出来都是垃圾。**xlsx 文档错误, 实际是统一小端 (LE)**。

### 2.1 查询帧 (相机 → 机器人)
| 偏移 | 大小 | 值 |
|---|---|---|
| 0–3 | 4B | `02 02 02 02` |
| 4–95 | 92B | `00` |

### 2.2 运动控制帧 (相机 → 机器人)
| 偏移 | 大小 | 类型 | 含义 |
|---|---|---|---|
| 0 | 1B | byte | 机器人品牌 (1=ABB, 2=KUKA, 11=Stäubli; Neura 用 2 占位) |
| 1 | 1B | byte | 编码标志 (样例里写 BE, 实际数据是 LE) |
| 2–3 | 2B | byte | 功能码 (未用) |
| **4–27** | 24B | f×6 | **关节角 1-6 (度)** |
| **28–39** | 12B | f×3 | **末端位置 X/Y/Z (mm)** |
| **40–51** | 12B | f×3 | **末端姿态 RX(C)/RY(B)/RZ(A) (度)** |
| 52 | 1B | i8 | 工作区域 (0–128) |
| 53 | 1B | i8 | 速度 (0–9, 9 档; 0 = 默认) |
| 54 | 1B | i8 | 过渡半径 (0–9) |
| **55** | 1B | i8 | **运动方式** (1=MoveAbsJ, 2=MoveJ, 3=MoveL) |
| 56 | 1B | i8 | 请求运动 (0=查询, 1=执行) |
| 57–60 | 4B | i32 | **目标点位编号 (Home=1)** |
| 61–76 | 16B | - | 进入工作区域信号 |
| 77–92 | 16B | - | 退出工作区域信号 |
| 93–95 | 3B | - | 占位 |

### 2.3 状态帧 (机器人 → 相机)
| 偏移 | 大小 | 类型 | 含义 |
|---|---|---|---|
| 0–3 | 4B | byte | 帧头 (`02 01 01 00`) |
| 4–27 | 24B | f×6 | **当前关节角 (度)** |
| 28–39 | 12B | f×3 | **当前末端 X/Y/Z (mm)** |
| 40–51 | 12B | f×3 | **当前末端 RX/RY/RZ (度)** |
| 52 | 1B | i8 | 工作状态 (0=空闲, 1=响应运动指令中) |
| 53 | 1B | i8 | 是否在原点 (0/1) |
| 54 | 1B | i8 | 是否急停 (0/1) |
| 55 | 1B | i8 | 是否在运动 (0/1) |
| 56 | 1B | i8 | 主程序是否启动 (0/1) |
| 57 | 1B | i8 | 工作区域 |
| 58 | 1B | i8 | 机器人异常 |
| 59 | 1B | i8 | 异常码 |
| 60–95 | 36B | - | 占位 |

## 3. 运动方式 → neurapy 映射

| 协议 | neurapy 调用 (PDF §5.1) | 说明 |
|---|---|---|
| 1 MoveAbsJ | `Robot.move_joint(target_joint=[...])` | 关节空间绝对运动 (用帧里的关节角) |
| 2 MoveJ    | `Robot.move_joint(target_pose=[...])` | 关节空间运动到目标位姿 (Neura 内部走 IK) |
| 3 MoveL    | `Robot.move_linear(target_pose=[...])` | 直线运动到目标位姿 |

> MoveJ 的关键点: spec 写"以目标位置的**末端位置**到达",**必须用 target_pose**。

## 4. 单位换算

协议用 **度 / 毫米 / 度**, neurapy 内部 **弧度 / 米 / 弧度**。脚本自动换算;若相机已按 Neura 单位发送,加 `--joint-unit rad --position-unit m --orientation-unit rad` 关掉。

## 5. 启动 point_client (生产 / 控制器)

```bash
# 控制器上 (neurapy 装好之后)
python point_client.py --camera-host 192.168.2.50 --camera-port 9000

# 调试
python point_client.py --camera-host 127.0.0.1 --camera-port 9000 -v
```

常用参数:
- `--camera-host` / `--camera-port`: 相机服务端
- `--robot-ip`: neurapy 的 `socket_address` (默认 `192.168.2.13`)
- `--joint-unit {deg,rad}` / `--position-unit {mm,m}` / `--orientation-unit {deg,rad}`: 协议单位
- `--speed-pct` / `--linear-speed`: 关节速度 (%) 和直线速度 (m/s) 的默认值
- `--connect-timeout`: TCP 拨号超时 (秒), 默认 10。**只**用于初始 `connect()`, 不影响后续 recv
- `--recv-timeout`: 等待相机下一帧超时 (秒), 默认 60。相机发帧间隔长(慢 / 网络抖动)就要调大, 设很大 (如 86400) 相当于阻塞 recv
- `--reconnect-initial` / `--reconnect-max`: 断线重连退避

## 6. 离线工具链 (macOS 都能跑)

### 6.1 parse_frame — 解 hex 字符串

不连机器人, 纯协议调试, 只依赖 stdlib + vision_protocol。

```bash
# 命令行参数
python parse_frame.py "02010100 2201abc1 c8c7c940 89beca42 ..."

# 从 stdin 读 (适合管道 / 文件)
echo "02010100 22 01 ab c1 ..." | python parse_frame.py
cat frame.hex | python parse_frame.py

# 无参也无 stdin -> 显示 usage
python parse_frame.py
```

自动判断:
- `02 02 02 02` 头 → 查询帧 (无数据字段, 提示直接回 build_status 即可)
- `02 01 01 00` 头 → 运动控制和状态两种解释都解 (因为这两种帧共用这个头, 区别在后段字段语义, 由你根据上下文判断)
- 自动 padding / truncating 到 96 字节, 缺字节会 warning

### 6.2 build_frame — 造测试报文

`parse_frame.py` 的反操作 — 输入十进制关节角 / TCP 位姿, 生成 96 字节 hex。可选直接 TCP 发送给相机。

**值必须用引号包住** (防 zsh glob, 不然 `[10, 20, ...]` 会被 shell 拆掉)。`--joints` / `--xyz` / `--rpy` 三种格式都行:

| 格式 | 例子 |
|---|---|
| 空格分隔 | `--joints "10 -20 30 0 40 0"` |
| 逗号分隔 | `--joints "10,-20,30,0,40,0"` |
| Python 列表 | `--joints "[10, -20, 30, 0, 40, 0]"` |

数量错会直接报错:
```
$ build_frame.py --joints "10 20 30"
build_frame.py: error: argument --joints: expected 6 values, got 3: [10.0, 20.0, 30.0]
```

参数:
- `--type {motion,query}` 帧类型 (默认 motion)
- `--joints VALS` 6 个关节角 (度), motion 必填
- `--xyz VALS` 末端位置 X Y Z (mm), motion 必填
- `--rpy VALS` 末端姿态 (度), 默认 `[0, 0, 0]`
- `--motion-type {1,2,3}` 1=MoveAbsJ, 2=MoveJ, 3=MoveL (默认 1)
- `--point-id N` 点位编号 (默认 1, Home)
- `--speed 0-9` 速度档 (默认 5)
- `--blend-radius 0-9` 过渡半径 (默认 0)
- `--work-area N` 工作区域 (默认 0)
- `--no-execute` request_motion=0 当查询用
- `--send-to HOST:PORT` TCP 发送 + 打印应答
- `--out {hex,hexdump,both}` 输出格式 (默认 hexdump, hex 是单行方便管道)

## 7. 样例输入 (copy-paste 即可)

```bash
cd /Users/adam/Documents/Codex/neurapy_socket_bridge

# 1) MoveAbsJ 到 Home
python3 build_frame.py \
    --joints "[0, 0, 0, 0, 0, 0]" \
    --xyz "[0, 0, 0]" \
    --point-id 1 --motion-type 1

# 2) MoveAbsJ 到你之前应答里的真实位姿
python3 build_frame.py \
    --joints "[-21.4, 6.3, 101.4, -1.2, -62.4, -0.9]" \
    --xyz "[692.6, -278.6, 346.7]" \
    --rpy "[-178.9, -2.9, 157.9]" \
    --point-id 2 --motion-type 1 --speed 5

# 3) MoveL 直线到指定位姿
python3 build_frame.py \
    --joints "[0, 0, 0, 0, 0, 0]" \
    --xyz "[500, 100, 300]" \
    --rpy "[3.14, 0, 3.14]" \
    --point-id 3 --motion-type 3 --speed 8

# 4) MoveJ 关节空间到位姿 (Neura 走 IK)
python3 build_frame.py \
    --joints "[0, 0, 0, 0, 0, 0]" \
    --xyz "[400, 0, 250]" \
    --rpy "[3.14, 0, 0]" \
    --point-id 4 --motion-type 2 --speed 6

# 5) 查询帧
python3 build_frame.py --type query

# 6) 闭环验证: build -> parse 验证回环 (要能解出相同数值)
python3 build_frame.py \
    --joints "[0, 0, 0, 0, 0, 0]" \
    --xyz "[500, 100, 300]" \
    --point-id 7 --out hex | xargs python3 parse_frame.py

# 7) 直接 TCP 发送给相机 (地址换成你的), 打印发送的 hex + 相机应答
python3 build_frame.py \
    --joints "[0, 0, 0, 0, 0, 0]" \
    --xyz "[0, 0, 0]" \
    --point-id 1 \
    --send-to 192.168.2.99:9000
```

## 8. 测试

```bash
python test_binary.py
```

5 个用例, 18 个 check, 全部内联 mock neurapy, 不依赖外部服务:
- xlsx 样例字节解析
- MoveJ 用 target_pose (走 IK)
- MoveAbsJ 用 target_joint
- MoveL 用 target_pose + move_linear
- 查询响应格式 / 字节序 / 单位

## 9. 已知限制

- **`connect_timeout` 和 `recv_timeout` 是分开的**。`socket.settimeout()` 一旦设了, 后面所有阻塞操作(connect/recv/send)共用。
  早期版本只用了一个 10s timeout, recv 阶段也会 10s 超时断开, 慢相机会反复重连。
  现在 `--connect-timeout 10` 只管初始握手, `--recv-timeout 60` (默认) 管后续等帧。
- xlsx 协议是**请求-响应**(1 帧入, 1 帧出), `move_joint` / `move_linear` 是阻塞的。所以状态帧里的 `is_moving` 永远是 0, `work_status` 永远是 0 — 相机只能事后查, 看不到运动中状态。
- 状态帧的 `at_origin` / `emergency_stop` / `main_program_started` / `work_area` / `exception` / `exception_code` 字段当前写死值 (Neura 没直接 API 暴露), 生产前要按业务实际映射。
- 帧里 `point_id` 当前只 log, **没有**去 Neura 点位库查 — 理想做法是 `move_joint(["P5"])` 查 `P5`, 库里没有再回退到帧里的 pose 数据。
- 协议里 `enter_area` / `exit_area` / `work_area` / `blend_radius` 都解析了但**未使用**, 生产前按业务需要加进 neurapy 调用。
- 机器人品牌码 `ROBOT_BRAND = 0x02` (KUKA) 是占位 (Neura 不在 xlsx 列表里), 上线前需要跟相机约定好。
- **macOS 不能装 neurapy** (PDF §4.2: 官方只支持 Ubuntu 18.04/20.04)。本机用 `parse_frame.py` / `build_frame.py` 做协议开发, 部署到 Linux 控制器跑 `point_client.py`。


## Debug UI (optional)

Web-based debug panel for the 96-byte binary protocol. Lives in `web/`. Zero new pip dependencies; macOS / Windows / Linux (Python 3.7+).

### Run

```bash
# Standalone UI + fake camera (no real point_client needed)
python web/run.py --protocol neurapy --auto-start-camera

# Full closed loop (point_client + mock neurapy via PYTHONPATH)
python web/run_debug.py --protocol neurapy

# Capture from real camera / point_client
python web/run.py --protocol neurapy --inspector-connect 192.168.2.50:9000
```

Browser: http://127.0.0.1:8765

### Add a new protocol

1. `cp web/protocols/_template.py /path/to/my_proto.py`
2. Set `FRAME_SIZE`, implement `classify` / `parse` / `build` / `schema`
3. `python web/run.py --protocol /path/to/my_proto.py:MyProtocol`

Or register a name: add `"myproj": "mymodule:MyProtocol"` to `web/protocols/REGISTRY`, then `--protocol myproj`.

### Test

```bash
python -m unittest discover -s web/tests
python -m unittest test_binary.py
bash scripts/check_platform.sh
```

See `docs/manual-test.md`.
