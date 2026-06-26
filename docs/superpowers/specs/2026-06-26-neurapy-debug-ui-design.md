# NeuraPY Debug UI — 设计文档

- 日期: 2026-06-26
- 项目: Ross98/NeuraPY
- 目标: 给 96 字节 TCP 协议 + neurapy bridge 一个浏览器调试面板,dev 机无真硬件联调

## 1. 范围

**In**:
- fake_camera: 96B 协议 TCP server(默认 :9000)
- fake_robot: 假 `neurapy.Robot` Python 模块,API 跟真 neurapy 一致
- inspector_client: 可选 TCP client,连真相机 / point_client 抓流量
- 浏览器 UI:连接管理 / 96B 帧检查器 / 帧构造器 / 实时状态栏
- SSE 实时推送 + REST 控制接口

**Out**:
- neurapy 库本身任何改动
- point_client.py 任何改动
- 真机器人 / 真相机集成测试
- 持久化日志(只内存,刷新即丢)
- 多浏览器协作 / 鉴权

## 2. 架构

**单进程**,Python `http.server.ThreadingHTTPServer` 启 `:8765`,三块:

- HTTP 处理器:静态文件 + REST + SSE 长连接
- 三个 daemon 线程:fake_camera / inspector_client(按需启停)
- 子进程包装器(可选):起 point_client,PYTHONPATH 注入 fake_neurapy

```
[Browser]  <-HTTP/SSE->  [web/run.py]
                         |- ThreadingHTTPServer :8765
                         |- fake_camera TCPServer :9000  (daemon thread)
                         |- inspector_client             (daemon thread, opt)
                         `- run_debug 启子进程:
                              point_client.py  (PYTHONPATH=./web/mock)
                                `- from neurapy.robot import Robot  ->  fake
                                    `- sidecar HTTP :8766
```

**File layout**:
```
neurapy_socket_bridge/
|- point_client.py         (不动)
|- vision_protocol.py      (不动)
|- parse_frame.py          (不动)
|- build_frame.py          (不动)
|- test_binary.py          (不动,继续做协议单元测)
|- web/                    <- 新增
|   |- __init__.py
|   |- run.py              (ThreadingHTTPServer 入口)
|   |- run_debug.py        (子进程 + PYTHONPATH 注入)
|   |- server.py           (HTTP handler + SSE)
|   |- state.py            (EventBus + 快照)
|   |- frame_router.py     (通用: partial frame 累积 + protocol.parse 包装)
|   |- protocol.py         (Protocol 抽象基类)
|   |- protocols/
|   |   |- __init__.py     (注册表: name -> "module:Class")
|   |   |- neurapy.py      (NeuraPY 适配,委托 vision_protocol)
|   |   `- _template.py    (新协议起步模板)
|   |- roles/
|   |   |- __init__.py
|   |   |- fake_camera.py
|   |   `- inspector_client.py
|   |- mock/
|   |   `- neurapy/
|   |       |- __init__.py
|   |       `- robot.py
|   |- static/
|   |   |- index.html
|   |   |- app.js
|   |   `- style.css
|   `- tests/
|       |- test_event_bus.py
|       |- test_mock_neurapy.py
|       |- test_sse_serialize.py
|       |- test_frame_router.py
|       |- test_snapshot.py
|       `- test_e2e_closed_loop.py
|- docs/
|   |- superpowers/specs/2026-06-26-neurapy-debug-ui-design.md
|   `- manual-test.md
`- README.md               (加 "Debug UI" 一节)
```

**依赖**:**零新增 pip**。只用 Python 3.7+ stdlib(`http.server`, `socketserver`, `queue`, `json`, `struct`, `threading`, `dataclasses`, `logging`, `argparse`, `collections`)。`vision_protocol` 已纯 stdlib。

**跨平台**:`http.server` / `socketserver` / `socket` 全平台。`web/` + `mock/neurapy/` 下禁 `os.fork` / `signal.SIGWINCH` / `/proc/` / `fcntl.`(grep 检查脚本加 CI)。README 加 "Supported platforms: macOS / Windows / Linux (Python 3.7+)"。

## 3. 协议抽象

`Protocol` 是本 UI 唯一关心的"项目差异"接口 — 项目只换这一个,其他全不动。

**`web/protocol.py`** — 抽象基类(PEP 544 structural typing):

```python
class Protocol(ABC):
    FRAME_SIZE: int  # 例: 96

    @abstractmethod
    def classify(self, frame: bytes) -> str:
        """返回帧类型名: 'query' | 'motion' | 'status' | <自定义> | 'unknown'"""

    @abstractmethod
    def parse(self, frame: bytes) -> dict:
        """返回 {"type": "<classify 结果>", "fields": {<name>: <value>, ...}}"""

    @abstractmethod
    def build(self, type: str, **fields) -> bytes:
        """字段 dict -> 原始字节流"""

    @property
    @abstractmethod
    def schema(self) -> dict:
        """返回 {
          "frames": {
            "<type>": {
              "label": "中文显示名",
              "fields": [
                {"name": "joints", "type": "list[float6]", "unit": "deg",
                 "offset": 4, "length": 24, "default": [0,0,0,0,0,0]},
                ...
              ]
            },
            ...
          }
        }"""
```

**字段类型枚举**:`int` / `float` / `bytes` / `list[int]` / `list[float]` / `list[float3]`(XYZ) / `list[float6]`(joints) / `enum{1,2,3}`(单选) / `enum{0,1}`(boolean)。前端按 type 渲染对应输入控件;`offset` + `length` 给 hex grid 高亮用;`unit` 在显示值时拼后缀。

**`web/protocols/__init__.py`** — 注册表:

```python
REGISTRY = {
    "neurapy": "web.protocols.neurapy:NeuraPYProtocol",
    # 新协议在这里挂一行即可
}

def load(name_or_path: str) -> Protocol:
    """--protocol 参数解析:
       - 'neurapy' / 'foo' -> REGISTRY[name] -> importlib 加载
       - './foo.py:MyProto' / '/abs/path/mod.py:Cls' -> 直接动态加载
       不传或找不到 -> 报错 + 列出 REGISTRY 名字"""
```

**`web/protocols/neurapy.py`** — NeuraPY 适配器。`vision_protocol.py` 0 改动(spec Out),这里做薄薄一层把它的方法映射到 `Protocol` 接口;`schema` 手写一次,把 NeuraPY 字段布局告诉前端。

**`web/protocols/_template.py`** — 新协议起步:

```python
from web.protocol import Protocol

class TemplateProtocol(Protocol):
    FRAME_SIZE = 0  # TODO
    def classify(self, frame: bytes) -> str: raise NotImplementedError
    def parse(self, frame: bytes) -> dict: raise NotImplementedError
    def build(self, type: str, **fields) -> bytes: raise NotImplementedError
    @property
    def schema(self) -> dict: return {"frames": {}}
```

新项目 `cp _template.py my_proto.py`,改 FRAME_SIZE / 4 个方法 / schema 即可。

**前端动态表单**:`app.js` 启动时 `GET /api/schema` 拿到 `protocol.schema`,根据 `frames` 字典动态生成"帧类型下拉"和"字段表单"。点击 [Build] 时把 `{type, fields}` 扔给 `/api/build`,返回的 hex 自动填到 hex override 框,再 [Send] 发出去。inspector / hex grid 同理 — `parse` 返回 dict + schema 的 `offset/length` 算高亮区间。

## 4. 三个角色

### 4.1 fake_camera (`web/roles/fake_camera.py`)

- `socketserver.ThreadingTCPServer(('0.0.0.0', 9000), Handler)`
- 收 `protocol.FRAME_SIZE` 字节 -> `protocol.classify` 判断类型 -> `protocol.parse` 解析 -> 推 `{kind:frame_in, src:fake_camera, data:{raw_hex, len, parsed:{type, fields}}}`
- 暴露 `send_bytes(raw96B)`,REST `/api/send` 调它
- 启停:`POST /api/connect` / `/api/disconnect` `{role:"fake_camera", port:9000}`

### 4.2 fake_robot = mock neurapy (`web/mock/neurapy/robot.py`)

`class Robot`,API 跟真 neurapy 一致(point_client 实际用到的 9 个方法,核过):

- `__init__(socket_address)` - 记地址,启 sidecar
- `robot_name` / `dof` - 属性
- `get_current_joint_angles() -> list[6]`
- `get_tcp_pose() -> [X,Y,Z,R,P,Y]`
- `is_robot_in_teach_mode() -> bool`
- `switch_to_automatic_mode()` - 幂等
- `power_on()` / `stop()` - 幂等
- `move_joint(target_joint|target_pose=..., speed, acceleration, current_joint_angles=...)`
- `move_linear(target_pose=..., speed, acceleration, current_joint_angles=...)`

内部 state:`self._joints_rad: list[6]`, `self._tcp: [X,Y,Z,R,P,Y]`。`move_*` 更新 state,`time.sleep(0.5)` 模拟运动,然后 `print(json.dumps({"event":"state", "joints":..., "tcp":..., "ts":...}))` 到 stdout。

**sidecar HTTP**(`__init__` 起 `http.server.HTTPServer(('127.0.0.1', 8766), SidecarHandler)` daemon 线程):

- `GET /state` -> JSON `{joints, tcp, is_moving}`
- `POST /set_pose` body `{joints?, tcp?}` - 强推状态(测异常 / 跳变)
- `POST /reach_target` body `{joints?, tcp?}` - 模拟无错到达
- `POST /simulate_error` body `{code, msg}` - 抛 `RobotError`,point_client 捕获 -> 状态帧 `error=1`

`--mock-sidecar-port` CLI 参数可改 8766,防端口冲突。

### 4.3 inspector_client (`web/roles/inspector_client.py`)

- 纯 TCP client,连 `host:port`(UI 配置)
- 收 `protocol.FRAME_SIZE` 字节 -> `protocol.classify` + `protocol.parse` -> 推 `{kind:frame_in, src:inspector, data:{raw_hex, len, parsed:{type, fields}}}`
- 断线退避:初 1s,x2,封顶 30s,推 `{kind:log, src:inspector, data:{msg:"retrying in 4s"}}`
- 默认关,`/api/connect {role:"inspector", host, port}` 启

## 5. 数据流

5 条路径:

1. **相机->UI**:fake_camera `recv(protocol.FRAME_SIZE)` -> frame_router 累积完整帧 -> `protocol.parse` -> EventBus.push -> SSE 广播
2. **UI 主动发帧**:UI `POST /api/send` -> 对应 role.send_bytes -> 路径 3 回流
3. **point_client->相机**:point_client send -> fake_camera.recv(FRAME_SIZE) -> 路径 1
4. **mock 状态**:point_client stdout JSON -> run_debug.py subprocess.Popen.stdout -> parse -> EventBus
5. **UI 操控 mock**:UI `POST /api/mock/*` -> web/run.py handler -> HTTP POST `127.0.0.1:8766` 直连 mock sidecar(同机 localhost,run_debug 不参与;若 sidecar 未起,返 503)

**EventBus**(`web/state.py`):
```python
from dataclasses import dataclass, asdict
import collections, queue

@dataclass
class Event:
    ts: float
    kind: str   # frame_in | frame_out | state | connect | disconnect | error | log
    src: str    # fake_camera | inspector | mock | point_client | ui
    data: dict

class EventBus:
    def __init__(self, maxlen: int = 2000):
        self._subs: list[queue.Queue] = []  # 每订阅者一个 Queue(maxsize=2000)
        self._snapshot: collections.deque = collections.deque(maxlen=200)
    def push(self, e: Event):
        self._snapshot.append(asdict(e))
        for q in self._subs:
            try: q.put_nowait(e)
            except queue.Full: q.get_nowait(); q.put_nowait(e)  # 丢最老
    def subscribe(self) -> Iterator[Event]: ...
    def snapshot(self) -> list[dict]: return list(self._snapshot)
```

**SSE 协议**:`text/event-stream`,每条事件:
```
data: {"ts":..., "kind":..., "src":..., "data":{...}}\n\n
```

**`frame_in` / `frame_out` payload**:
```json
{
  "raw_hex": "02 01 01 00 ...",
  "len": 96,
  "parsed": {"type": "query|motion|status", "fields": {...vision_protocol 输出...}},
  "peer": "127.0.0.1:52341"
}
```

**`state` payload**:
```json
{"joints_rad": [0,0,0,0,0,0], "tcp": [X,Y,Z,R,P,Y], "is_moving": false}
```

**`connect` / `disconnect`**: `{peer, reason: null|"..."}`

**`error`**: `{msg, fatal: false}`

**`log`**: `{msg}`(普通日志事件,非错误)

## 6. REST 接口

| Method | Path | Body | Resp |
|---|---|---|---|
| GET | `/` | - | 静态 `index.html` |
| GET | `/static/*` | - | 静态资源 |
| GET | `/api/snapshot` | - | `{state, events: [...last 200], connections: [...]}` |
| GET | `/api/stream` | - | SSE 长连接 |
| POST | `/api/connect` | `{role, host?, port?}` | `{ok, role, port}` |
| POST | `/api/disconnect` | `{role}` | `{ok}` |
| POST | `/api/send` | `{target, hex}` | `{ok, raw_hex, len}` |
| GET | `/api/schema` | - | `protocol.schema` 整树(给前端动态表单) |
| POST | `/api/build` | `{type, fields: {...}}` | `{hex, parsed: {type, fields}, raw_hex, len}` |
| POST | `/api/parse` | `{hex}` | `{type, fields, raw_hex, len}` |
| POST | `/api/mock/set_pose` | `{joints?, tcp?}` | `{ok, state}` |
| POST | `/api/mock/reach_target` | `{joints?, tcp?}` | `{ok, state}` |
| POST | `/api/mock/simulate_error` | `{code, msg}` | `{ok}` |

`/api/send` 的 `target` in {`fake_camera`, `inspector`},缺省 `fake_camera`。hex 由前端先调 `/api/build` 拿到,再 [Send] 发出去。

## 7. 前端布局

```
+-----------------------------------------------------------------------------+
|  NeuraPY Debug UI   [o live]   fake_camera :9000 (1 client)  mock o running |
+--------------+----------------------------------------+----------------------+
|  CONNECTS    |  FRAME INSPECTOR                       |  FRAME BUILDER       |
|              |                                        |                      |
| +- Connect -+|  +--- 96B Frame (highlighted) ---+    | Type: [Motionv]      |
| | Role      ||  | 00 01 02 03 04 05 06 07 08 ... |    | Motion: [MoveAbsJv]  |
| | v camera  ||  | 02 01 01 00 [22 01 ab c1] ...  |    |                      |
| | Port 9000 ||  |                               |    | Joints (deg):        |
| | [Start]   ||  | click byte -> highlight field |    | J1 [  0.0] J2 [  0.0]|
| | [Stop]    ||  | + show details                 |    | J3 [  0.0] J4 [  0.0]|
| +-----------+|  +-------------------------------+    | J5 [  0.0] J6 [  0.0]|
|              |                                        |                      |
| +- Roles ---+|  +--- Parsed --------------------+    | XYZ (mm):            |
| | o Camera  ||  | Header: 02 01 01 00 (status)  |    | X  [  0.0] Y [  0.0] |
| | o Robot   ||  | Joints (deg): 55.4, ...       |    | Z  [  0.0]           |
| | o Inspct  ||  | Position (mm): 692.6, ...     |    |                      |
| +-----------+|  | Speed: 5  Blend: 0            |    | RPY (deg):           |
|              |  | Point ID: 2                   |    | RX [   0 ] RY [   0 ]|
| +- Targets -+|  | Work area: 0                  |    | RZ [   0 ]           |
| | Send to:  ||  +-------------------------------+    |                      |
| | v camera  ||                                        | Speed [5] Blend [0]  |
| | [Send]    ||  +--- Log (last 200) -----------+     | Work [0] PtID [1]   |
| +-----------+|  | > 12:34:56 IN  motion pt#2  |     |                      |
|              |  | > 12:34:56 OUT status OK    |     | [Build] [Send]       |
|              |  | ...                          |     | [Send & watch reply] |
|              |  +-------------------------------+     |                      |
|              |                                        | Hex override:        |
|              |                                        | +------------------+ |
|              |                                        | | 02 01 01 00 ...  | |
|              |                                        | +------------------+ |
+--------------+----------------------------------------+----------------------+
|  STATE  J1=0.0  J2=0.0  ...  TCP X=0 Y=0 Z=0 RX=0 RY=0 RZ=0   o idle  o ok |
+-----------------------------------------------------------------------------+
```

四块职责:

- **左(连接/发送)**:启停 fake_camera / inspector,选发往哪端
- **中(检查器)**:96B hex grid(点格亮字段)+ parsed 视图 + 时间线日志
- **右(构造器)**:schema 动态生成的字段表单 -> `/api/build` 拿 hex -> `/api/send` 发出去;或粘 hex 改字节发送
- **底(状态栏)**:mock robot 当前 joints/tcp,运动/异常标志位 - SSE 实时刷

`app.js` 单文件:启动 `GET /api/schema` 拿到字段定义,按 schema 动态渲染表单;开 `EventSource('/api/stream')` 按 kind 分发到三面板 + toast。`style.css` 深色 + 等宽字体,hex 看着舒服。

## 8. 错误处理

**帧层**:
- 非 96B 累积到 buffer,200ms 超时清零;`len != 96` -> `kind=error, data={msg:"short frame 48B"}`
- 96B 但 header 未知 -> `kind=error, data={msg:"unknown header 0xDEADBEEF"}`,不阻断
- `parse_motion` / `parse_status` 抛 -> try/except + raw_hex,继续收

**TCP 层**:
- fake_camera client 断 -> `kind=disconnect, data={peer, reason}`,继续 accept
- 端口占用 -> 启动失败 -> `kind=error, fatal:true`,UI 顶栏红字
- inspector 断 -> 退避重试(1s x2,封顶 30s),`kind=log`

**mock**:
- `RobotError`(IK 失败 / 目标不可达)-> point_client 捕获 -> 状态帧 `error=1, code=N` -> inspector 日志 + 状态栏红字
- sidecar 参数错(非 list 等)-> HTTP 400,UI toast
- 未知方法调用 -> `AttributeError`,point_client 抛,日志记录,bridge 不崩

**point_client 子进程**:
- 退出非 0 -> run_debug 自动重启(等 2s),`kind=log, src:run_debug, data:{msg:"point_client exited 1, restarting"}`
- stdout JSON 坏 -> `kind=error, data:{msg:"bad json: ..."}`,不退出

**SSE 客户端**:
- 满队列(2000)-> 丢最老 + `kind=error, data={msg:"client lag, dropped 47 events"}`
- 客户端断 -> server 端关连接,清 queue

**UI 反馈统一**:
- `kind=error` -> 右上 toast(3s 自动消失)+ 顶栏红点计数
- `kind=disconnect` -> 连接指示灯转灰
- fatal -> toast 持久 + 详情按钮(看 raw_hex / 堆栈)

## 9. 测试

**不动**:`test_binary.py`(协议层回归),`python -m unittest` 跑。

**新增 `web/tests/`**:

- `test_event_bus.py` - push/sub 顺序、满队列丢最老、多订阅者独立计数、snapshot deque 封顶 200
- `test_protocol_interface.py` - `TemplateProtocol` 触发 `NotImplementedError`;NeuraPYProtocol 满足 `isinstance(_, Protocol)`,classify/parse/build/schema 四件套都返回正确类型
- `test_protocol_registry.py` - `load("neurapy")` 拿到 NeuraPYProtocol 实例;`load("./mod.py:Cls")` 动态加载;`load("nope")` 报错列名字
- `test_mock_neurapy.py` - `move_joint` 改 state、`move_linear` 同、`power_on`/`stop` 幂等、未知方法 `AttributeError`、stdout JSON 格式
- `test_sse_serialize.py` - event -> JSON -> `data: <json>\n\n`,转义换行 / 中文 / 反斜杠
- `test_frame_router.py` - FRAME_SIZE 字节 -> kind 识别 + 字段解析,坏 header error 路径,partial frame 累积
- `test_snapshot.py` - `/api/snapshot` 返回结构,200 条事件回放顺序
- `test_e2e_closed_loop.py` - in-process 闭环(不起端口),< 1s:
  - 喂 motion 帧给 point_client -> 触发 mock.move_joint -> 收 status 帧 -> 断言字节对 + 断言 SSE 事件流顺序

**跨平台冒烟**:
- 脚本 `scripts/check_platform.sh`:`grep -rE "os\.fork|signal\.SIGWINCH|/proc/|fcntl\." web/ mock/` 必须空
- 失败非零退出
- README 加 "Supported platforms: macOS / Windows / Linux (Python 3.7+)"

**手测清单** `docs/manual-test.md`:

- [ ] `python web/run.py` 打开 `http://127.0.0.1:8765` 看到三面板
- [ ] 启 fake_camera -> `python point_client.py --camera-host 127.0.0.1 --camera-port 9000` 接入,UI 时间线出 "connect"
- [ ] UI 构 MotionAbsJ -> Send -> 1s 内时间线出 "frame_in" status
- [ ] inspector 模式连自己 fake_camera -> 抓双向流
- [ ] 拔 point_client 网络 -> UI 显 disconnect,恢复后自动 reconnect
- [ ] 粘错 hex 到 builder hex override -> Send -> 状态栏红字 + toast
- [ ] mock pose 改 X=10000 -> point_client 构状态帧 -> UI TCP 数字刷新
- [ ] Win / Mac / Linux 各跑一次 `python -m unittest discover -s web/tests` 全绿

## 10. 启动方式

```bash
# 全套闭环(dev 机无真硬件)
cd neurapy_socket_bridge
python web/run_debug.py
# 浏览器 http://127.0.0.1:8765

# 仅 inspector(真 point_client 跑别处,UI 抓流量)
python web/run.py --protocol neurapy --inspector-connect 192.168.2.50:9000
# 浏览器 http://127.0.0.1:8765

# 换协议(自定义项目)
python web/run.py --protocol ~/projects/foo/foo_protocol.py:FooProtocol

# 跑测试
python -m unittest discover -s web/tests
python -m unittest test_binary.py

# 跨平台检查
bash scripts/check_platform.sh
```

## 11. 风险 & 遗留

- **启动顺序**:`run_debug` 必须先 bind fake_camera :9000,再起 point_client 子进程。point_client 自带重连,首次 connect 失败会被吞,1-2s 后自动接上,可接受
- **sidecar 端口冲突**:`--mock-sidecar-port` CLI 可改 8766
- **浏览器 SSE 断连**:系统休眠 / 网络切换时 EventSource 会断,自动重连,中间事件丢(已知,接受;要持久化见 §1 Out)
- **mock neurapy 简化**:真 neurapy 还有 `get_robot_state` / `get_error_code` 等边角方法,本 spec 不实现。point_client 当前代码只用 §4.2 列的 9 个方法,核过。后续如 point_client 加调用,补 mock 实现
- **零依赖的代价**:`http.server` 单文件路由需要手写 dispatcher(几行 if/elif),接受
- **新协议门槛**:实现 4 个方法 (classify/parse/build/schema) + 写一次单测。CRC / 条件字段 / 跨字段校验都能写(`Protocol` 是 Python ABC,不是 schema 限制),但比纯配置门槛高。换来的是 NeuraPY 那种"编码标志写 BE 实际 LE" / "运动方式决定字段语义" 的项目怪癖都能表达
