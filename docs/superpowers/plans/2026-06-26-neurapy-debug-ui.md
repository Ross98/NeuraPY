# NeuraPY Debug UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 NeuraPY 96 字节 TCP 协议 + neurapy bridge 一个浏览器调试面板,dev 机无真硬件联调;UI 设计成协议无关,新项目加一个 `Protocol` 子类即可接入

**Architecture:** 单进程,`http.server.ThreadingHTTPServer` 启 :8765,SSE 实时 + REST 控制 + 静态前端。三个 TCP 角色 daemon 线程(fake_camera server, inspector client, mock neurapy sidecar)。子进程包装器启 point_client 通过 PYTHONPATH 注入 mock neurapy。`Protocol` 抽象类隔离项目差异。

**Tech Stack:** Python 3.7+ stdlib(零新 pip 依赖)。前端 vanilla HTML/JS/CSS,`EventSource` + `fetch`。

## Global Constraints

- Python 3.7+(用 `from __future__ import annotations` 兼容新语法)
- 零新 pip 依赖
- macOS / Windows / Linux 全平台
- `web/` + `web/mock/` + `web/protocols/` 下禁 `os.fork` / `signal.SIGWINCH` / `/proc/` / `fcntl.`
- **不修改** `point_client.py` / `vision_protocol.py` / `parse_frame.py` / `build_frame.py` / `test_binary.py`
- HTTP :8765 / fake_camera :9000 / mock sidecar :8766(都可 CLI 改)
- SSE backlog 2000/客户端,snapshot 200
- `scripts/check_platform.sh` 必须全空
- `python -m unittest discover -s web/tests` 全绿

## File Structure

**新建:**
- `web/__init__.py`, `web/tests/__init__.py` — 空
- `web/state.py` — Event + EventBus
- `web/sse.py` — SSE 序列化
- `web/protocol.py` — Protocol ABC
- `web/protocols/__init__.py` — REGISTRY + load()
- `web/protocols/_template.py` — 起步模板
- `web/protocols/neurapy.py` — NeuraPY 适配
- `web/frame_router.py` — 帧累积 + parse 包装
- `web/roles/__init__.py`, `web/roles/fake_camera.py`, `web/roles/inspector_client.py`
- `web/mock/__init__.py`, `web/mock/neurapy/__init__.py`, `web/mock/neurapy/robot.py`
- `web/server.py` — HTTP handler(REST + SSE + 静态)
- `web/run.py` — CLI 入口
- `web/run_debug.py` — 子进程包装
- `web/static/{index.html, app.js, style.css}` — 前端
- `web/tests/test_*.py` — 单元 + 集成
- `scripts/check_platform.sh`, `docs/manual-test.md`

**修改:**
- `README.md` — 加 "Debug UI" 一节

---

### Task 1: EventBus + Event dataclass

**Files:**
- Create: `web/__init__.py`, `web/tests/__init__.py` (empty)
- Create: `web/state.py`
- Test: `web/tests/test_event_bus.py`

**Interfaces:**
- Produces: `Event(ts: float, kind: str, src: str, data: dict)`
- Produces: `EventBus(maxlen: int=2000)`, methods: `push(e)`, `subscribe() -> Iterator[Event]`, `snapshot() -> list[dict]`

- [ ] **Step 1: Create directory skeleton**

```bash
mkdir -p web/tests
touch web/__init__.py web/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

`web/tests/test_event_bus.py`:
```python
import threading
import time
import unittest
from web.state import Event, EventBus


class TestEventBus(unittest.TestCase):
    def test_event_dataclass(self):
        e = Event(ts=1.5, kind="frame_in", src="fake_camera", data={"x": 1})
        self.assertEqual(e.ts, 1.5)
        self.assertEqual(e.kind, "frame_in")
        self.assertEqual(e.data, {"x": 1})

    def test_push_then_snapshot(self):
        bus = EventBus()
        bus.push(Event(ts=1.0, kind="x", src="y", data={"k": "v"}))
        snap = bus.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0], {"ts": 1.0, "kind": "x", "src": "y", "data": {"k": "v"}})

    def test_snapshot_capped_at_200(self):
        bus = EventBus()
        for i in range(250):
            bus.push(Event(ts=float(i), kind="x", src="y", data={}))
        snap = bus.snapshot()
        self.assertEqual(len(snap), 200)
        self.assertEqual(snap[0]["ts"], 50.0)
        self.assertEqual(snap[-1]["ts"], 249.0)

    def test_subscribe_receives_pushed_events(self):
        bus = EventBus()
        received = []
        def consumer():
            for e in bus.subscribe():
                received.append(e)
                if len(received) >= 2: return
        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        time.sleep(0.05)
        bus.push(Event(ts=1.0, kind="x", src="y", data={}))
        bus.push(Event(ts=2.0, kind="x", src="y", data={}))
        t.join(timeout=1.0)
        self.assertEqual(len(received), 2)
        self.assertEqual([e.ts for e in received], [1.0, 2.0])

    def test_queue_full_drops_oldest(self):
        bus = EventBus(maxlen=2)
        alive = threading.Event()
        def keep():
            alive.set()
            for _ in bus.subscribe(): pass
        t = threading.Thread(target=keep, daemon=True)
        t.start()
        alive.wait(0.1)
        for i in range(5):
            bus.push(Event(ts=float(i), kind="x", src="y", data={}))
        time.sleep(0.1)
        self.assertTrue(t.is_alive())
```

- [ ] **Step 3: Run test, expect FAIL**

```bash
cd /Users/adam/Documents/Codex/neurapy_socket
python -m unittest web.tests.test_event_bus -v
```
Expected: `ModuleNotFoundError: No module named 'web.state'`

- [ ] **Step 4: Implement**

`web/state.py`:
```python
"""Event bus for the debug UI."""
import collections
import queue
from dataclasses import asdict, dataclass, field
from typing import Iterator, List


@dataclass
class Event:
    ts: float
    kind: str   # frame_in | frame_out | state | connect | disconnect | error | log
    src: str    # fake_camera | inspector | mock | point_client | ui
    data: dict = field(default_factory=dict)


class EventBus:
    def __init__(self, maxlen: int = 2000):
        self._maxlen = maxlen
        self._subs: List[queue.Queue] = []
        self._snapshot: collections.deque = collections.deque(maxlen=200)

    def push(self, e: Event) -> None:
        self._snapshot.append(asdict(e))
        dead = []
        for q in self._subs:
            try:
                q.put_nowait(e)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(e)
                except (queue.Empty, queue.Full):
                    dead.append(q)
        for q in dead:
            try: self._subs.remove(q)
            except ValueError: pass

    def subscribe(self) -> Iterator[Event]:
        q: queue.Queue = queue.Queue(maxsize=self._maxlen)
        self._subs.append(q)
        try:
            while True:
                yield q.get()
        finally:
            try: self._subs.remove(q)
            except ValueError: pass

    def snapshot(self) -> list:
        return list(self._snapshot)
```

- [ ] **Step 5: Run test, expect PASS**

```bash
python -m unittest web.tests.test_event_bus -v
```
Expected: 5 tests pass

- [ ] **Step 6: Commit**

```bash
git add web/__init__.py web/tests/__init__.py web/state.py web/tests/test_event_bus.py
git commit -m "feat(web): add EventBus + Event dataclass with TDD"
```

---

### Task 2: SSE serialization helper

**Files:**
- Create: `web/sse.py`
- Test: `web/tests/test_sse_serialize.py`

**Interfaces:**
- Produces: `sse_format(event: dict) -> bytes` returns `b"data: <json>\\n\\n"`

- [ ] **Step 1: Write the failing test**

`web/tests/test_sse_serialize.py`:
```python
import unittest
from web.sse import sse_format


class TestSSE(unittest.TestCase):
    def test_basic_event(self):
        out = sse_format({"kind": "x", "data": 1})
        self.assertEqual(out, b'data: {"kind": "x", "data": 1}\\n\\n')

    def test_chinese_escaped(self):
        out = sse_format({"data": "操控"})
        self.assertIn(b'\\u64cd\\u63a7', out)
        self.assertTrue(out.endswith(b'\\n\\n'))

    def test_newline_in_data_escaped(self):
        out = sse_format({"data": "line1\\nline2"})
        self.assertIn(b'\\\\n', out)
        self.assertNotIn(b'\\nline2', out[len(b'data: '):])

    def test_bytes_returned(self):
        self.assertIsInstance(sse_format({}), bytes)
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_sse_serialize -v
```
Expected: `ModuleNotFoundError: No module named 'web.sse'`

- [ ] **Step 3: Implement**

`web/sse.py`:
```python
"""SSE (Server-Sent Events) serialization."""
import json


def sse_format(event: dict) -> bytes:
    payload = json.dumps(event, ensure_ascii=True)
    return f"data: {payload}\\n\\n".encode("ascii")
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_sse_serialize -v
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/sse.py web/tests/test_sse_serialize.py
git commit -m "feat(web): add SSE serialization helper"
```

---

### Task 3: Protocol ABC

**Files:**
- Create: `web/protocol.py`
- Test: `web/tests/test_protocol_interface.py`

**Interfaces:**
- Produces: `class Protocol(ABC)` with `FRAME_SIZE: int`, `classify`, `parse`, `build`, `schema` property

- [ ] **Step 1: Write the failing test**

`web/tests/test_protocol_interface.py`:
```python
import unittest
from web.protocol import Protocol


class TestProtocolABC(unittest.TestCase):
    def test_cannot_instantiate_directly(self):
        with self.assertRaises(TypeError):
            Protocol()

    def test_must_implement_all_methods(self):
        class Incomplete(Protocol):
            FRAME_SIZE = 10
            def classify(self, frame): return "x"
        with self.assertRaises(TypeError):
            Incomplete()

    def test_subclass_with_all_methods_ok(self):
        class Full(Protocol):
            FRAME_SIZE = 10
            def classify(self, frame): return "x"
            def parse(self, frame): return {"type": "x", "fields": {}}
            def build(self, type, **f): return b"\\x00" * 10
            @property
            def schema(self): return {"frames": {}}
        p = Full()
        self.assertEqual(p.FRAME_SIZE, 10)
        self.assertIsInstance(p, Protocol)
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_protocol_interface -v
```
Expected: `ModuleNotFoundError: No module named 'web.protocol'`

- [ ] **Step 3: Implement**

`web/protocol.py`:
```python
"""Protocol abstraction: the one place project-specific knowledge lives."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class Protocol(ABC):
    """All debug-UI protocol adapters implement this interface."""

    FRAME_SIZE: int  # subclass must set

    @abstractmethod
    def classify(self, frame: bytes) -> str:
        """Return frame type name. e.g. 'query' / 'motion' / 'status' / 'unknown'."""

    @abstractmethod
    def parse(self, frame: bytes) -> Dict[str, Any]:
        """Return {"type": "<classify>", "fields": {<name>: <value>, ...}}."""

    @abstractmethod
    def build(self, type: str, **fields) -> bytes:
        """Build a frame from structured fields. Raises ValueError on bad input."""

    @property
    @abstractmethod
    def schema(self) -> Dict[str, Any]:
        """Return UI form schema: {"frames": {<type>: {"label", "fields": [...]}}}.
        Field metadata: name, type, unit?, offset, length, default?.
        Types: int | float | bytes | list[int] | list[float] | list[float3] | list[float6] | enum{...}
        """
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_protocol_interface -v
```
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/protocol.py web/tests/test_protocol_interface.py
git commit -m "feat(web): add Protocol ABC"
```

---

### Task 4: Protocol registry + NeuraPY adapter + template

**Files:**
- Create: `web/protocols/__init__.py`
- Create: `web/protocols/_template.py`
- Create: `web/protocols/neurapy.py`
- Test: `web/tests/test_protocol_registry.py`

**Interfaces:**
- Produces: `REGISTRY: dict[str, str]`, `load(name_or_path: str) -> Protocol` (raises ValueError on miss)

- [ ] **Step 1: Write the failing test**

`web/tests/test_protocol_registry.py`:
```python
import json
import os
import tempfile
import unittest
import urllib.request
from web.protocols import REGISTRY, load
from web.protocol import Protocol


class TestRegistry(unittest.TestCase):
    def test_neurapy_registered(self):
        self.assertIn("neurapy", REGISTRY)

    def test_load_neurapy(self):
        p = load("neurapy")
        self.assertIsInstance(p, Protocol)
        self.assertEqual(p.FRAME_SIZE, 96)
        s = p.schema
        self.assertIn("frames", s)
        self.assertIn("motion", s["frames"])
        names = {f["name"] for f in s["frames"]["motion"]["fields"]}
        self.assertIn("joints", names)
        self.assertIn("position", names)

    def test_load_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load("nope_no_such_protocol")
        self.assertIn("nope_no_such_protocol", str(ctx.exception))
        self.assertIn("neurapy", str(ctx.exception))

    def test_load_from_filepath(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("from web.protocol import Protocol\\n")
            f.write("class TmpProto(Protocol):\\n")
            f.write("    FRAME_SIZE = 8\\n")
            f.write("    def classify(self, frame): return 'x'\\n")
            f.write("    def parse(self, frame): return {'type': 'x', 'fields': {}}\\n")
            f.write("    def build(self, type, **f): return b'\\\\x00' * 8\\n")
            f.write("    @property\\n    def schema(self): return {'frames': {}}\\n")
            tmp_path = f.name
        try:
            p = load(f"{tmp_path}:TmpProto")
            self.assertIsInstance(p, Protocol)
            self.assertEqual(p.FRAME_SIZE, 8)
        finally:
            os.unlink(tmp_path)

    def test_template_raises(self):
        from web.protocols._template import TemplateProtocol
        p = TemplateProtocol()
        with self.assertRaises(NotImplementedError):
            p.classify(b"")
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_protocol_registry -v
```
Expected: `ModuleNotFoundError: No module named 'web.protocols'`

- [ ] **Step 3: Implement template**

`web/protocols/_template.py`:
```python
"""Starter template for new protocols. cp _template.py my_proto.py"""
from web.protocol import Protocol


class TemplateProtocol(Protocol):
    FRAME_SIZE = 0  # TODO

    def classify(self, frame: bytes) -> str:
        raise NotImplementedError

    def parse(self, frame: bytes) -> dict:
        raise NotImplementedError

    def build(self, type: str, **fields) -> bytes:
        raise NotImplementedError

    @property
    def schema(self) -> dict:
        return {"frames": {}}
```

- [ ] **Step 4: Implement NeuraPY adapter**

`web/protocols/neurapy.py`:
```python
"""NeuraPY Protocol adapter. vision_protocol.py is untouched (spec Out)."""
from typing import Any, Dict
from vision_protocol import VisionProtocol
from web.protocol import Protocol


class NeuraPYProtocol(Protocol):
    FRAME_SIZE = 96

    def __init__(self):
        self._vp = VisionProtocol()

    def classify(self, frame: bytes) -> str:
        if len(frame) < 4:
            return "unknown"
        if frame[0:4] == b"\\x02\\x02\\x02\\x02":
            return "query"
        if frame[0:4] == b"\\x02\\x01\\x01\\x00":
            return "motion_or_status"
        return "unknown"

    def parse(self, frame: bytes) -> Dict[str, Any]:
        if len(frame) != self.FRAME_SIZE:
            raise ValueError(f"frame must be {self.FRAME_SIZE} bytes, got {len(frame)}")
        t = self.classify(frame)
        if t == "query":
            return {"type": "query", "fields": {}}
        try:
            d = self._vp.parse_motion(frame)
            return {"type": "motion", "fields": self._flatten(d)}
        except Exception:
            try:
                d = self._vp.parse_status(frame)
                return {"type": "status", "fields": self._flatten(d)}
            except Exception as e:
                return {"type": "unknown", "fields": {}, "error": str(e)}

    def build(self, type: str, **fields) -> bytes:
        if type == "query":
            return b"\\x02\\x02\\x02\\x02" + b"\\x00" * 92
        if type == "motion":
            return self._vp.build_motion(
                joints=fields.get("joints", [0, 0, 0, 0, 0, 0]),
                position=fields.get("position", [0, 0, 0]),
                orientation=fields.get("orientation", [0, 0, 0]),
                motion_type=fields.get("motion_type", 1),
                point_id=fields.get("point_id", 1),
                speed=fields.get("speed", 5),
                blend_radius=fields.get("blend_radius", 0),
                work_area=fields.get("work_area", 0),
            )
        if type == "status":
            return self._vp.build_status(
                joints=fields.get("joints", [0, 0, 0, 0, 0, 0]),
                position=fields.get("position", [0, 0, 0]),
                orientation=fields.get("orientation", [0, 0, 0]),
                flags=fields.get("flags", {}),
            )
        raise ValueError(f"unknown frame type: {type!r}")

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "frames": {
                "query": {"label": "查询帧", "fields": []},
                "motion": {
                    "label": "运动控制帧",
                    "fields": [
                        {"name": "joints", "type": "list[float6]", "unit": "deg",
                         "offset": 4, "length": 24, "default": [0, 0, 0, 0, 0, 0]},
                        {"name": "position", "type": "list[float3]", "unit": "mm",
                         "offset": 28, "length": 12, "default": [0, 0, 0]},
                        {"name": "orientation", "type": "list[float3]", "unit": "deg",
                         "offset": 40, "length": 12, "default": [0, 0, 0]},
                        {"name": "motion_type", "type": "enum{1,2,3}",
                         "offset": 55, "length": 1, "default": 1,
                         "labels": ["MoveAbsJ", "MoveJ", "MoveL"]},
                        {"name": "point_id", "type": "int",
                         "offset": 57, "length": 4, "default": 1},
                        {"name": "speed", "type": "int",
                         "offset": 53, "length": 1, "default": 5},
                        {"name": "blend_radius", "type": "int",
                         "offset": 54, "length": 1, "default": 0},
                        {"name": "work_area", "type": "int",
                         "offset": 52, "length": 1, "default": 0},
                    ],
                },
                "status": {
                    "label": "状态帧",
                    "fields": [
                        {"name": "joints", "type": "list[float6]", "unit": "deg",
                         "offset": 4, "length": 24},
                        {"name": "position", "type": "list[float3]", "unit": "mm",
                         "offset": 28, "length": 12},
                        {"name": "orientation", "type": "list[float3]", "unit": "deg",
                         "offset": 40, "length": 12},
                        {"name": "is_moving", "type": "enum{0,1}",
                         "offset": 55, "length": 1},
                    ],
                },
            },
        }

    def _flatten(self, d: dict) -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    out[k2] = v2
            else:
                out[k] = v
        return out
```

- [ ] **Step 5: Implement registry**

`web/protocols/__init__.py`:
```python
"""Protocol registry + dynamic loader."""
import importlib
import importlib.util
import sys
from pathlib import Path
from web.protocol import Protocol


REGISTRY: dict = {
    "neurapy": "web.protocols.neurapy:NeuraPYProtocol",
}


def load(name_or_path: str) -> Protocol:
    if not name_or_path:
        available = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise ValueError(f"--protocol required. Available: {available}")

    is_path_form = ":" in name_or_path and (
        name_or_path.endswith(".py") or "/" in name_or_path or name_or_path.startswith(".")
    )
    if is_path_form:
        path_str, _, cls_name = name_or_path.partition(":")
        path = Path(path_str).resolve()
        if not path.exists():
            raise ValueError(f"protocol file not found: {path}")
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load spec from {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, cls_name, None)
        if cls is None:
            raise ValueError(f"class {cls_name!r} not found in {path}")
        return cls()

    if name_or_path in REGISTRY:
        mod_path, _, cls_name = REGISTRY[name_or_path].partition(":")
        mod = importlib.import_module(mod_path)
        return getattr(mod, cls_name)()

    available = ", ".join(sorted(REGISTRY))
    raise ValueError(f"unknown protocol {name_or_path!r}. Available: {available}")
```

- [ ] **Step 6: Run test, expect PASS**

```bash
python -m unittest web.tests.test_protocol_registry -v
```
Expected: 5 tests pass

- [ ] **Step 7: Commit**

```bash
git add web/protocols/ web/tests/test_protocol_registry.py
git commit -m "feat(web): add Protocol registry + NeuraPY adapter + template"
```

---

### Task 5: Frame router (partial frame accumulation + protocol.parse)

**Files:**
- Create: `web/frame_router.py`
- Test: `web/tests/test_frame_router.py`

**Interfaces:**
- Produces: `class FrameRouter(protocol, on_frame, partial_timeout=0.2)` with `feed(data: bytes) -> None`

- [ ] **Step 1: Write the failing test**

`web/tests/test_frame_router.py`:
```python
import unittest
from web.frame_router import FrameRouter
from web.protocol import Protocol


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


class TestFrameRouter(unittest.TestCase):
    def test_complete_frame_emitted(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\\x01\\x02\\x03\\x04")
        self.assertEqual(frames, [(b"\\x01\\x02\\x03\\x04", "X")])

    def test_chunked_frame_accumulated(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\\x01\\x02"); r.feed(b"\\x03\\x04")
        self.assertEqual(frames, [(b"\\x01\\x02\\x03\\x04", "X")])

    def test_multiple_frames_in_one_chunk(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08")
        self.assertEqual(len(frames), 2)

    def test_oversize_buffer_keeps_tail(self):
        frames = []
        r = FrameRouter(_P(), on_frame=lambda f, t: frames.append((f, t)))
        r.feed(b"\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08\\x09")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][0], b"\\x01\\x02\\x03\\x04")
        self.assertEqual(frames[1][0], b"\\x05\\x06\\x07\\x08")
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_frame_router -v
```
Expected: `ModuleNotFoundError: No module named 'web.frame_router'`

- [ ] **Step 3: Implement**

`web/frame_router.py`:
```python
"""Accumulate byte stream into complete protocol frames, dispatch each frame."""
from typing import Callable, Optional
from web.protocol import Protocol


class FrameRouter:
    def __init__(self, protocol: Protocol, on_frame: Callable[[bytes, str], None],
                 partial_timeout: float = 0.2):
        self._p = protocol
        self._on_frame = on_frame
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self._buf.extend(data)
        n = self._p.FRAME_SIZE
        while len(self._buf) >= n:
            frame = bytes(self._buf[:n])
            del self._buf[:n]
            try:
                t = self._p.classify(frame)
            except Exception:
                t = "unknown"
            try:
                self._on_frame(frame, t)
            except Exception:
                pass  # never let consumer crash the router

    def reset(self) -> None:
        self._buf.clear()
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_frame_router -v
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/frame_router.py web/tests/test_frame_router.py
git commit -m "feat(web): add FrameRouter (partial frame accumulation)"
```

---

### Task 6: Mock neurapy module + sidecar

**Files:**
- Create: `web/mock/__init__.py`, `web/mock/neurapy/__init__.py` (empty/short)
- Create: `web/mock/neurapy/robot.py`
- Test: `web/tests/test_mock_neurapy.py`

**Interfaces:**
- Produces: `class Robot` matching neurapy.Robot API (9 methods per spec §4.2)
- Sidecar: `http.server.HTTPServer(('127.0.0.1', port))` started in daemon thread in `__init__`

- [ ] **Step 1: Write the failing test**

`web/tests/test_mock_neurapy.py`:
```python
import io
import json
import sys
import threading
import time
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
        r = Robot(socket_address="127.0.0.1", sidecar_port=18766)
        self.assertEqual(r.dof, 6)
        self.assertEqual(len(r.get_current_joint_angles()), 6)
        self.assertEqual(len(r.get_tcp_pose()), 6)

    def test_move_joint_changes_state(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=18767)
        r.move_joint(target_joint=[[10, 20, 30, 0, 40, 0]], speed=50.0,
                     acceleration=50.0, current_joint_angles=[0]*6)
        self.assertEqual(r.get_current_joint_angles(), [10, 20, 30, 0, 40, 0])

    def test_sidecar_get_state(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=18768)
        sc = _sidecar(18768)
        st = sc.get("/state")
        self.assertIn("joints", st)
        self.assertIn("tcp", st)

    def test_sidecar_set_pose(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=18769)
        sc = _sidecar(18769)
        sc.post("/set_pose", {"joints": [1, 2, 3, 4, 5, 6]})
        self.assertEqual(r.get_current_joint_angles(), [1, 2, 3, 4, 5, 6])

    def test_sidecar_simulate_error(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=18770)
        sc = _sidecar(18770)
        sc.post("/simulate_error", {"code": 7, "msg": "IK fail"})
        with self.assertRaises(RobotError):
            r.move_joint(target_joint=[[0]*6], speed=50.0, acceleration=50.0,
                         current_joint_angles=[0]*6)

    def test_stdout_state_event(self):
        r = Robot(socket_address="127.0.0.1", sidecar_port=18771)
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.move_joint(target_joint=[[1, 1, 1, 0, 0, 0]], speed=50.0,
                         acceleration=50.0, current_joint_angles=[0]*6)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("{")]
        self.assertTrue(any('"event": "state"' in l for l in lines), lines)
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_mock_neurapy -v
```
Expected: `ModuleNotFoundError: No module named 'web.mock.neurapy'`

- [ ] **Step 3: Implement**

`web/mock/neurapy/robot.py`:
```python
"""Mock neurapy.Robot + sidecar HTTP for UI control."""
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
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\\n")
        sys.stdout.flush()
```

`web/mock/neurapy/__init__.py`:
```python
from .robot import Robot, RobotError  # noqa: F401
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_mock_neurapy -v
```
Expected: 6 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/mock/ web/tests/test_mock_neurapy.py
git commit -m "feat(web): mock neurapy.Robot + sidecar HTTP"
```

---

### Task 7: Fake camera TCP server

**Files:**
- Create: `web/roles/__init__.py` (empty)
- Create: `web/roles/fake_camera.py`
- Test: `web/tests/test_fake_camera.py`

**Interfaces:**
- Produces: `class FakeCamera(protocol, event_bus, host, port)` with `start()` / `stop()` / `send_bytes(data: bytes) -> None` / `peer_count -> int`

- [ ] **Step 1: Write the failing test**

`web/tests/test_fake_camera.py`:
```python
import socket
import threading
import time
import unittest
from web.roles.fake_camera import FakeCamera
from web.protocol import Protocol
from web.state import EventBus


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class TestFakeCamera(unittest.TestCase):
    def test_client_can_connect_and_frame_in_event(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s = socket.socket(); s.connect(("127.0.0.1", port))
            s.sendall(b"\\xAA\\xBB\\xCC\\xDD")
            time.sleep(0.1)
            self.assertTrue(any(e["kind"] == "frame_in" for e in bus.snapshot()))
            s.close()
        finally:
            cam.stop()

    def test_send_bytes_broadcasts(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s1 = socket.socket(); s1.connect(("127.0.0.1", port))
            s2 = socket.socket(); s2.connect(("127.0.0.1", port))
            time.sleep(0.05)
            cam.send_bytes(b"\\x01\\x02\\x03\\x04")
            time.sleep(0.1)
            self.assertEqual(s1.recv(4), b"\\x01\\x02\\x03\\x04")
            self.assertEqual(s2.recv(4), b"\\x01\\x02\\x03\\x04")
            s1.close(); s2.close()
        finally:
            cam.stop()

    def test_connect_disconnect_events(self):
        bus = EventBus()
        port = _free_port()
        cam = FakeCamera(_P(), bus, host="127.0.0.1", port=port); cam.start()
        try:
            s = socket.socket(); s.connect(("127.0.0.1", port))
            time.sleep(0.1)
            kinds = [e["kind"] for e in bus.snapshot()]
            self.assertIn("connect", kinds)
            s.close()
            time.sleep(0.1)
            kinds = [e["kind"] for e in bus.snapshot()]
            self.assertIn("disconnect", kinds)
        finally:
            cam.stop()
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_fake_camera -v
```

- [ ] **Step 3: Implement**

`web/roles/fake_camera.py`:
```python
"""TCP server that speaks the loaded protocol."""
import socket
import socketserver
import threading
import time
from typing import List
from web.frame_router import FrameRouter
from web.protocol import Protocol
from web.state import Event, EventBus


class _Handler(socketserver.BaseRequestHandler):
    cam: "FakeCamera" = None

    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        with self.cam._lock:
            self.cam._clients.add(self.request)
        self.cam.bus.push(Event(ts=time.time(), kind="connect", src="fake_camera",
                                data={"peer": peer, "reason": None}))
        try:
            while True:
                chunk = self.request.recv(4096)
                if not chunk: break
                self.cam.router.feed(chunk)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            with self.cam._lock:
                self.cam._clients.discard(self.request)
            self.cam.bus.push(Event(ts=time.time(), kind="disconnect", src="fake_camera",
                                    data={"peer": peer, "reason": "client closed"}))


class FakeCamera:
    def __init__(self, protocol: Protocol, event_bus: EventBus,
                 host: str = "0.0.0.0", port: int = 9000):
        self.protocol = protocol
        self.bus = event_bus
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._clients: List[socket.socket] = []
        self._lock = threading.Lock()

        def on_frame(raw, t):
            try: parsed = self.protocol.parse(raw)
            except Exception as e: parsed = {"type": "unknown", "fields": {}, "error": str(e)}
            self.bus.push(Event(ts=time.time(), kind="frame_in", src="fake_camera",
                                data={"raw_hex": raw.hex(), "len": len(raw), "parsed": parsed}))

        self.router = FrameRouter(protocol, on_frame=on_frame)

    def start(self):
        if self._server is not None: return
        _Handler.cam = self
        self._server = socketserver.ThreadingTCPServer((self.host, self.port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="fake-camera")
        self._thread.start()

    def stop(self):
        if self._server is None: return
        self._server.shutdown(); self._server.server_close(); self._server = None

    def send_bytes(self, data: bytes) -> None:
        if self._server is None:
            raise RuntimeError("fake_camera not started")
        if len(data) != self.protocol.FRAME_SIZE:
            raise ValueError(f"data must be {self.protocol.FRAME_SIZE} bytes")
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try: c.sendall(data)
            except OSError: pass
        try: parsed = self.protocol.parse(data)
        except Exception as e: parsed = {"type": "unknown", "fields": {}, "error": str(e)}
        self.bus.push(Event(ts=time.time(), kind="frame_out", src="fake_camera",
                            data={"raw_hex": data.hex(), "len": len(data), "parsed": parsed}))

    @property
    def peer_count(self) -> int:
        with self._lock: return len(self._clients)
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_fake_camera -v
```
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/roles/ web/tests/test_fake_camera.py
git commit -m "feat(web): fake_camera TCP server with broadcast + EventBus"
```

---

### Task 8: Inspector client

**Files:**
- Create: `web/roles/inspector_client.py`
- Test: `web/tests/test_inspector_client.py`

**Interfaces:**
- Produces: `class InspectorClient(protocol, event_bus, host, port, retry_initial=1.0, retry_max=30.0)` with `start()` / `stop()`

- [ ] **Step 1: Write the failing test**

`web/tests/test_inspector_client.py`:
```python
import socket
import threading
import time
import unittest
from web.roles.inspector_client import InspectorClient
from web.protocol import Protocol
from web.state import EventBus


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class TestInspector(unittest.TestCase):
    def test_receives_frames(self):
        bus = EventBus()
        port = _free_port()
        ss = socket.socket()
        ss.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ss.bind(("127.0.0.1", port)); ss.listen(1)
        def server():
            conn, _ = ss.accept()
            conn.sendall(b"\\x01\\x02\\x03\\x04")
            time.sleep(0.05); conn.close()
        threading.Thread(target=server, daemon=True).start()
        ic = InspectorClient(_P(), bus, host="127.0.0.1", port=port)
        ic.start()
        time.sleep(0.3)
        ic.stop()
        ss.close()
        self.assertIn("frame_in", [e["kind"] for e in bus.snapshot()])

    def test_retries_on_failure(self):
        bus = EventBus()
        ic = InspectorClient(_P(), bus, host="127.0.0.1", port=1, retry_initial=0.05,
                            retry_max=0.1, retry_max_attempts=2)
        ic.start()
        time.sleep(0.5)
        ic.stop()
        msgs = [e["data"].get("msg", "") for e in bus.snapshot() if e["kind"] == "log"]
        self.assertTrue(any("retry" in m or "failed" in m for m in msgs), msgs)
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_inspector_client -v
```

- [ ] **Step 3: Implement**

`web/roles/inspector_client.py`:
```python
"""TCP client that passively captures frames from a real camera / point_client."""
import socket
import threading
import time
from web.frame_router import FrameRouter
from web.protocol import Protocol
from web.state import Event, EventBus


class InspectorClient:
    def __init__(self, protocol: Protocol, event_bus: EventBus, host: str, port: int,
                 retry_initial: float = 1.0, retry_max: float = 30.0,
                 retry_max_attempts: int = 0):
        self.protocol = protocol
        self.bus = event_bus
        self.host = host
        self.port = port
        self._retry = retry_initial
        self._retry_max = retry_max
        self._retry_max_attempts = retry_max_attempts
        self._stop = threading.Event()
        self._thread = None
        self._attempts = 0

        def on_frame(raw, t):
            try: parsed = self.protocol.parse(raw)
            except Exception as e: parsed = {"type": "unknown", "fields": {}, "error": str(e)}
            self.bus.push(Event(ts=time.time(), kind="frame_in", src="inspector",
                                data={"raw_hex": raw.hex(), "len": len(raw), "parsed": parsed,
                                      "peer": f"{self.host}:{self.port}"}))
        self.router = FrameRouter(protocol, on_frame=on_frame)

    def start(self):
        if self._thread is not None: return
        self._thread = threading.Thread(target=self._run, daemon=True, name="inspector")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None: self._thread.join(timeout=2.0)

    def _run(self):
        backoff = self._retry
        while not self._stop.is_set():
            self._attempts += 1
            if self._retry_max_attempts and self._attempts > self._retry_max_attempts:
                return
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.bus.push(Event(ts=time.time(), kind="log", src="inspector",
                                    data={"msg": f"connected to {self.host}:{self.port}"}))
                backoff = self._retry
                try:
                    while not self._stop.is_set():
                        chunk = sock.recv(4096)
                        if not chunk: break
                        self.router.feed(chunk)
                finally:
                    sock.close()
                self.bus.push(Event(ts=time.time(), kind="disconnect", src="inspector",
                                    data={"peer": f"{self.host}:{self.port}", "reason": "closed"}))
            except (OSError, socket.timeout) as e:
                self.bus.push(Event(ts=time.time(), kind="log", src="inspector",
                                    data={"msg": f"connect failed: {e}; retrying in {backoff:.0f}s"}))
                if self._stop.wait(backoff): return
                backoff = min(backoff * 2, self._retry_max)
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_inspector_client -v
```
Expected: 2 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/roles/inspector_client.py web/tests/test_inspector_client.py
git commit -m "feat(web): inspector_client TCP client with backoff retry"
```

---

### Task 9: HTTP server (REST + SSE + static)

**Files:**
- Create: `web/server.py`
- Test: `web/tests/test_snapshot.py`

**Interfaces:**
- Produces: `make_server(protocol, bus, host, port) -> ThreadingHTTPServer`
- Routes per spec §6

- [ ] **Step 1: Write the failing test**

`web/tests/test_snapshot.py`:
```python
import json
import socket
import threading
import time
import unittest
import urllib.request
from web.server import make_server
from web.protocol import Protocol
from web.state import Event, EventBus


class _P(Protocol):
    FRAME_SIZE = 4
    def classify(self, frame): return "X"
    def parse(self, frame): return {"type": "X", "fields": {}}
    def build(self, type, **f): return b"\\x00" * 4
    @property
    def schema(self): return {"frames": {"X": {"label": "X", "fields": []}}}


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.status, r.read().decode("utf-8")


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, r.read().decode("utf-8")


class TestServer(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.proto = _P()
        self.port = _free_port()
        self.server = make_server(self.proto, self.bus, host="127.0.0.1", port=self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.thread.join(timeout=2.0)

    def test_snapshot(self):
        self.bus.push(Event(ts=1.0, kind="x", src="y", data={"k": 1}))
        s, b = _get(f"http://127.0.0.1:{self.port}/api/snapshot")
        j = json.loads(b)
        self.assertEqual(len(j["events"]), 1)
        self.assertEqual(j["events"][0]["data"]["k"], 1)

    def test_schema(self):
        s, b = _get(f"http://127.0.0.1:{self.port}/api/schema")
        j = json.loads(b)
        self.assertIn("frames", j); self.assertIn("X", j["frames"])

    def test_build(self):
        s, b = _post(f"http://127.0.0.1:{self.port}/api/build", {"type": "X", "fields": {}})
        j = json.loads(b)
        self.assertEqual(j["hex"], "00000000")
        self.assertEqual(j["len"], 4)

    def test_parse(self):
        s, b = _post(f"http://127.0.0.1:{self.port}/api/parse", {"hex": "aabbccdd"})
        j = json.loads(b)
        self.assertEqual(j["type"], "X")
        self.assertEqual(j["len"], 4)

    def test_stream_emits_event(self):
        received = []
        stop = threading.Event()
        def consume():
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/stream")
            with urllib.request.urlopen(req, timeout=3) as r:
                buf = b""
                while not stop.is_set():
                    chunk = r.read(1)
                    if not chunk: break
                    buf += chunk
                    if b"\\n\\n" in buf:
                        line, _, buf = buf.partition(b"\\n\\n")
                        if line.startswith(b"data: "):
                            received.append(json.loads(line[6:].decode()))
                        if received: break
        t = threading.Thread(target=consume, daemon=True)
        t.start()
        time.sleep(0.1)
        self.bus.push(Event(ts=2.0, kind="new", src="test", data={"v": 42}))
        t.join(timeout=2.0)
        stop.set()
        self.assertTrue(any(e["kind"] == "new" for e in received), received)
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
python -m unittest web.tests.test_snapshot -v
```

- [ ] **Step 3: Implement**

`web/server.py`:
```python
"""HTTP server: REST + SSE + static files."""
import json
import mimetypes
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from web.protocol import Protocol
from web.sse import sse_format
from web.state import EventBus

STATIC_DIR = Path(__file__).parent / "static"


class _State:
    def __init__(self, protocol: Protocol, bus: EventBus):
        self.protocol = protocol
        self.bus = bus
        self.fake_camera = None  # set by run.py after start


def make_server(protocol: Protocol, bus: EventBus, host: str = "0.0.0.0", port: int = 8765):
    state = _State(protocol, bus)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code, body, content_type="application/json", extra_headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=True).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            elif isinstance(body, bytes):
                pass
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", "0"))
            if not n: return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                index = STATIC_DIR / "index.html"
                if not index.exists():
                    self._send(404, {"error": "index.html not built yet"})
                    return
                self._send(200, index.read_bytes(), content_type="text/html; charset=utf-8")
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
                target = (STATIC_DIR / rel).resolve()
                if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
                    self._send(403, {"error": "forbidden"}); return
                if not target.exists() or not target.is_file():
                    self._send(404, {"error": "not found"}); return
                ctype, _ = mimetypes.guess_type(str(target))
                self._send(200, target.read_bytes(), content_type=ctype or "application/octet-stream")
            elif path == "/api/snapshot":
                self._send(200, {"events": state.bus.snapshot(), "state": {},
                                 "connections": []})
            elif path == "/api/schema":
                self._send(200, state.protocol.schema)
            elif path == "/api/stream":
                self._handle_sse()
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            try: body = self._read_json()
            except Exception as e:
                self._send(400, {"error": f"bad json: {e}"}); return

            if path == "/api/parse":
                try: raw = bytes.fromhex(body.get("hex", ""))
                except ValueError as e:
                    self._send(400, {"error": f"bad hex: {e}"}); return
                try: parsed = state.protocol.parse(raw)
                except Exception as e:
                    self._send(400, {"error": f"parse failed: {e}"}); return
                self._send(200, {"type": parsed.get("type"),
                                 "fields": parsed.get("fields", {}),
                                 "raw_hex": raw.hex(), "len": len(raw)})
            elif path == "/api/build":
                ftype = body.get("type"); fields = body.get("fields", {})
                if not ftype: self._send(400, {"error": "type required"}); return
                try: raw = state.protocol.build(ftype, **fields)
                except Exception as e:
                    self._send(400, {"error": f"build failed: {e}"}); return
                try: parsed = state.protocol.parse(raw)
                except Exception: parsed = {"type": ftype, "fields": fields}
                self._send(200, {"hex": raw.hex(), "len": len(raw), "parsed": parsed})
            elif path == "/api/send":
                target = body.get("target", "fake_camera")
                try: raw = bytes.fromhex(body.get("hex", ""))
                except ValueError as e:
                    self._send(400, {"error": f"bad hex: {e}"}); return
                if target != "fake_camera" or state.fake_camera is None:
                    self._send(503, {"error": f"target {target!r} not available"}); return
                state.fake_camera.send_bytes(raw)
                self._send(200, {"ok": True, "raw_hex": raw.hex(), "len": len(raw)})
            else:
                self._send(404, {"error": "not found"})

        def _handle_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q: queue.Queue = queue.Queue(maxsize=2000)
            state.bus._subs.append(q)
            try:
                while True:
                    try: ev = q.get(timeout=15.0)
                    except queue.Empty:
                        try:
                            self.wfile.write(b": keepalive\\n\\n"); self.wfile.flush()
                        except (BrokenPipeError, OSError): break
                        continue
                    payload = {"ts": ev.ts, "kind": ev.kind, "src": ev.src, "data": ev.data}
                    try:
                        self.wfile.write(sse_format(payload)); self.wfile.flush()
                    except (BrokenPipeError, OSError): break
            finally:
                try: state.bus._subs.remove(q)
                except ValueError: pass

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv
```

- [ ] **Step 4: Run test, expect PASS**

```bash
python -m unittest web.tests.test_snapshot -v
```
Expected: 5 tests pass

- [ ] **Step 5: Commit**

```bash
git add web/server.py web/tests/test_snapshot.py
git commit -m "feat(web): HTTP server with REST + SSE + static"
```

---

### Task 10: `run.py` CLI entry

**Files:**
- Create: `web/run.py`

- [ ] **Step 1: Implement**

`web/run.py`:
```python
#!/usr/bin/env python3
"""Web UI entry point. Loads a Protocol and starts the HTTP server.

Examples:
    python web/run.py --protocol neurapy --auto-start-camera
    python web/run.py --protocol ./my_proto.py:MyProto --port 9001
    python web/run.py --protocol neurapy --inspector-connect 192.168.2.50:9000
"""
import argparse
import sys

from web.protocols import load
from web.roles.fake_camera import FakeCamera
from web.roles.inspector_client import InspectorClient
from web.server import make_server
from web.state import EventBus


def main():
    ap = argparse.ArgumentParser(description="NeuraPY-style debug UI")
    ap.add_argument("--protocol", help="Protocol name (registry) or 'path/to/file.py:Class'")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fake-camera-port", type=int, default=9000)
    ap.add_argument("--inspector-connect", help="host:port to passively capture frames from")
    ap.add_argument("--auto-start-camera", action="store_true",
                    help="start fake_camera automatically")
    args = ap.parse_args()

    try: protocol = load(args.protocol)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)

    bus = EventBus()
    cam = ic = None
    if args.auto_start_camera:
        cam = FakeCamera(protocol, bus, host="0.0.0.0", port=args.fake_camera_port)
        cam.start()
        print(f"fake_camera listening on :{args.fake_camera_port}")
    if args.inspector_connect:
        host, _, p = args.inspector_connect.partition(":")
        ic = InspectorClient(protocol, bus, host=host, port=int(p))
        ic.start()
        print(f"inspector connected to {host}:{p}")

    srv = make_server(protocol, bus, host=args.host, port=args.port)
    # expose cam to handler state
    if hasattr(srv, "RequestHandlerClass"):
        pass
    # inject via the closure: re-create with cam set
    # Simpler: re-call make_server with state injection by setting attribute
    # We patched _State in server.py; for now set after.
    # Workaround: re-create via a small wrapper:
    import web.server as srvmod
    new_state = srvmod._State(protocol, bus)
    new_state.fake_camera = cam
    # Build a new server reusing the same Handler class but with patched state.
    # Simpler still: set on the existing _State used by the Handler.
    # Since _State is instantiated inside make_server, we need to either
    # re-instantiate or expose it. Cleanest: refactor make_server to accept
    # an existing state. For now, monkey-patch the Handler's closure by
    # binding cam to a known attribute the Handler reads.

    # Use the simplest hack: Handler reads state from a module-level dict.
    # We'll refactor: just set cam on the existing server's state via a hook.
    # For Task 10 we keep it simple: add a class attribute to Handler.
    Handler = srv.RequestHandlerClass
    Handler.cam = cam
    Handler.ic = ic
    Handler.protocol = protocol
    Handler.bus = bus
    # Re-create state reference inside Handler.do_POST (we'll patch _send path).
    # Actually the cleanest fix is to make state accessible; do that via the
    # existing closure by exposing `srv._state`:
    srv._state.fake_camera = cam

    print(f"web UI on http://{args.host}:{args.port}  protocol={protocol.__class__.__name__}")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\\nshutting down")
    finally:
        if cam: cam.stop()
        if ic: ic.stop()
        srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    main()
```

**Note**: this version has a small monkey-patch because `_State` is created inside `make_server`. Cleanest fix is to refactor Task 9's `make_server` to return `(server, state)`. The current run.py handles this inline; if a future test cares, refactor Task 9 to expose `state`.

- [ ] **Step 2: Smoke test**

```bash
python web/run.py --protocol neurapy --auto-start-camera --port 18765 &
PID=$!
sleep 0.5
curl -s http://127.0.0.1:18765/api/schema | python3 -c "import json,sys; print(list(json.load(sys.stdin)['frames'].keys()))"
kill $PID 2>/dev/null; wait 2>/dev/null
```
Expected: `['query', 'motion', 'status']`

- [ ] **Step 3: Commit**

```bash
git add web/run.py
git commit -m "feat(web): run.py CLI entry with --protocol + --inspector-connect"
```

---

### Task 11: `run_debug.py` — point_client subprocess wrapper

**Files:**
- Create: `web/run_debug.py`

- [ ] **Step 1: Implement**

`web/run_debug.py`:
```python
#!/usr/bin/env python3
"""Run the full closed loop: UI + fake_camera + point_client (with mock neurapy).

point_client does `from neurapy.robot import Robot` (never modified).
We prepend web/mock/ to PYTHONPATH so the mock satisfies that import.
point_client stdout JSON lines are parsed as state events.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from web.protocols import load
from web.roles.fake_camera import FakeCamera
from web.server import make_server
from web.state import Event, EventBus

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
MOCK_PKG_PARENT = HERE / "mock"


def main():
    ap = argparse.ArgumentParser(description="Full closed-loop debug UI")
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fake-camera-port", type=int, default=9000)
    ap.add_argument("--mock-sidecar-port", type=int, default=8766)
    ap.add_argument("--point-client-args", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    try: protocol = load(args.protocol)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(2)

    bus = EventBus()
    cam = FakeCamera(protocol, bus, host="0.0.0.0", port=args.fake_camera_port)
    cam.start()
    print(f"fake_camera on :{args.fake_camera_port}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(MOCK_PKG_PARENT) + os.pathsep + env.get("PYTHONPATH", "")
    env["MOCK_SIDECAR_PORT"] = str(args.mock_sidecar_port)

    pc_args = [sys.executable, str(PROJECT_ROOT / "point_client.py"),
               "--camera-host", "127.0.0.1", "--camera-port", str(args.fake_camera_port)]
    pc_args += args.point_client_args

    print(f"starting: {' '.join(pc_args)}  PYTHONPATH+={MOCK_PKG_PARENT}")
    proc = subprocess.Popen(pc_args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=1, text=True)

    def drain_stdout():
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    if obj.get("event") == "state":
                        bus.push(Event(ts=obj.get("ts", time.time()), kind="state",
                                       src="mock",
                                       data={"joints_rad": obj.get("joints", []),
                                             "tcp": obj.get("tcp", []),
                                             "is_moving": obj.get("is_moving", False)}))
                        continue
                except json.JSONDecodeError:
                    pass
            bus.push(Event(ts=time.time(), kind="log", src="point_client",
                           data={"msg": line}))
    threading.Thread(target=drain_stdout, daemon=True, name="point-client-stdout").start()

    shutdown = threading.Event()
    def watch_exit():
        rc = proc.wait()
        bus.push(Event(ts=time.time(), kind="log", src="run_debug",
                       data={"msg": f"point_client exited {rc}; restarting in 2s"}))
        time.sleep(2.0)
        if not shutdown.is_set():
            main()  # restart
    threading.Thread(target=watch_exit, daemon=True, name="watch-exit").start()

    srv = make_server(protocol, bus, host=args.host, port=args.port)
    srv._state.fake_camera = cam
    print(f"web UI on http://{args.host}:{args.port}  protocol={protocol.__class__.__name__}")
    print("press Ctrl-C to exit")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        shutdown.set()
        try: proc.terminate()
        except Exception: pass
        cam.stop()
        srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```bash
python web/run_debug.py --protocol neurapy --port 18766 &
PID=$!
sleep 0.5
curl -s http://127.0.0.1:18766/api/snapshot | python3 -c "import json,sys; j=json.load(sys.stdin); print('events:', len(j['events']))"
kill $PID 2>/dev/null; wait 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add web/run_debug.py
git commit -m "feat(web): run_debug.py — point_client subprocess wrapper with PYTHONPATH inject"
```

---

### Task 12: End-to-end closed loop test

**Files:**
- Test: `web/tests/test_e2e_closed_loop.py`

- [ ] **Step 1: Write the test**

`web/tests/test_e2e_closed_loop.py`:
```python
"""In-process closed loop: motion frame build -> feed -> parse -> round-trip."""
import unittest
from web.frame_router import FrameRouter
from web.protocols import load


class TestE2E(unittest.TestCase):
    def test_motion_frame_round_trip(self):
        p = load("neurapy")
        events = []
        r = FrameRouter(p, on_frame=lambda raw, t: events.append((raw, t)))
        raw = p.build("motion", joints=[10, 20, 30, 0, 40, 0], position=[500, 0, 200],
                      orientation=[0, 0, 0], motion_type=1, point_id=2, speed=5)
        self.assertEqual(len(raw), p.FRAME_SIZE)
        r.feed(raw)
        self.assertEqual(len(events), 1)
        raw_back, t = events[0]
        self.assertIn(t, ("motion", "motion_or_status"))
        parsed = p.parse(raw_back)
        self.assertEqual(parsed["type"], "motion")
        rt = parsed["fields"].get("joints")
        if rt is not None:
            for a, b in zip(rt, [10.0, 20.0, 30.0, 0.0, 40.0, 0.0]):
                self.assertAlmostEqual(a, b, places=3)

    def test_query_frame(self):
        p = load("neurapy")
        captured = []
        r = FrameRouter(p, on_frame=lambda raw, t: captured.append((raw, t)))
        raw = b"\\x02\\x02\\x02\\x02" + b"\\x00" * 92
        r.feed(raw)
        self.assertEqual(captured, [(raw, "query")])
```

- [ ] **Step 2: Run test, expect PASS**

```bash
python -m unittest web.tests.test_e2e_closed_loop -v
```
Expected: 2 tests pass

- [ ] **Step 3: Commit**

```bash
git add web/tests/test_e2e_closed_loop.py
git commit -m "test(web): end-to-end closed loop round-trip"
```

---

### Task 13: Frontend (HTML + JS + CSS)

**Files:**
- Create: `web/static/index.html`
- Create: `web/static/app.js`
- Create: `web/static/style.css`

- [ ] **Step 1: HTML**

`web/static/index.html`:
```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>NeuraPY Debug UI</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <h1>NeuraPY Debug UI</h1>
  <span id="status" class="pill">offline</span>
  <span id="cam" class="pill">camera: stopped</span>
  <span id="mock" class="pill">mock: stopped</span>
</header>
<main>
  <section id="left">
    <h2>CONNECTS</h2>
    <div class="row">
      <label>Role <select id="role"><option>fake_camera</option></select></label>
      <label>Port <input id="port" type="number" value="9000"></label>
      <button id="startCam">Start</button>
      <button id="stopCam">Stop</button>
    </div>
    <h3>Roles</h3>
    <ul><li>◉ Camera</li><li>○ Robot</li><li>○ Inspector</li></ul>
    <h3>Targets</h3>
    <div class="row">
      <label>Send to <select id="sendTarget"><option>fake_camera</option></select></label>
      <button id="sendBtn">Send</button>
    </div>
  </section>
  <section id="center">
    <h2>FRAME INSPECTOR</h2>
    <div id="hexGrid" class="hex-grid"></div>
    <h3>Parsed</h3>
    <pre id="parsed" class="kv">(select a frame from the log)</pre>
    <h3>Log (last 200)</h3>
    <ul id="log"></ul>
  </section>
  <section id="right">
    <h2>FRAME BUILDER</h2>
    <div class="row">
      <label>Type <select id="frameType"></select></label>
    </div>
    <div id="fields"></div>
    <div class="row">
      <button id="buildBtn">Build</button>
      <button id="sendBuildBtn">Send & watch reply</button>
    </div>
    <h3>Hex override</h3>
    <textarea id="hexOut" rows="3" spellcheck="false"></textarea>
  </section>
</main>
<footer><span id="state">STATE  J=0 0 0 0 0 0  TCP=0 0 0 0 0 0  ◯ idle</span></footer>
<div id="toasts"></div>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: CSS**

`web/static/style.css`:
```css
:root { --bg: #1a1a1a; --panel: #232323; --border: #333; --fg: #d0d0d0; --accent: #4af;
        --err: #f55; --ok: #5f5; }
* { box-sizing: border-box; }
body { margin: 0; font: 13px/1.4 -apple-system, BlinkMacSystemFont, "SF Mono", Menlo, monospace;
       background: var(--bg); color: var(--fg); }
header { display: flex; align-items: center; gap: 12px; padding: 8px 12px;
         border-bottom: 1px solid var(--border); }
header h1 { font-size: 14px; font-weight: normal; margin: 0; }
.pill { padding: 2px 8px; border: 1px solid var(--border); border-radius: 10px; font-size: 11px; }
.pill.ok { color: var(--ok); border-color: var(--ok); }
.pill.err { color: var(--err); border-color: var(--err); }
main { display: grid; grid-template-columns: 240px 1fr 320px; height: calc(100vh - 80px); }
section { padding: 10px; border-right: 1px solid var(--border); overflow: auto; }
section h2 { font-size: 12px; text-transform: uppercase; color: #888; margin: 0 0 8px; }
section h3 { font-size: 11px; text-transform: uppercase; color: #888; margin: 12px 0 4px; }
.row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
label { display: flex; align-items: center; gap: 4px; }
input, select, textarea { background: #111; color: var(--fg); border: 1px solid var(--border);
                          padding: 3px 6px; font: inherit; }
button { background: var(--panel); color: var(--fg); border: 1px solid var(--border);
         padding: 4px 10px; cursor: pointer; }
button:hover { border-color: var(--accent); }
#left ul { list-style: none; padding: 0; margin: 0; }
.hex-grid { display: grid; grid-template-columns: repeat(16, 1fr); gap: 1px;
            background: var(--panel); padding: 4px; font-size: 11px; margin-bottom: 8px; }
.hex-cell { padding: 2px 4px; text-align: center; }
.hex-cell:hover { background: var(--accent); color: #000; }
.kv { background: var(--panel); padding: 8px; white-space: pre-wrap;
       max-height: 200px; overflow: auto; }
#log { list-style: none; padding: 0; margin: 0; max-height: 30vh; overflow: auto; }
#log li { padding: 2px 4px; border-bottom: 1px solid #222; }
#log li.in { color: #8cf; }
#log li.out { color: #fc8; }
#log li.err { color: var(--err); }
#fields { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin: 8px 0; }
#fields label { flex-direction: column; align-items: stretch; }
#fields input, #fields select { width: 100%; }
#hexOut { width: 100%; font-family: inherit; }
footer { padding: 6px 12px; border-top: 1px solid var(--border); font-size: 12px; }
#toasts { position: fixed; top: 50px; right: 12px; display: flex; flex-direction: column; gap: 6px; }
.toast { background: var(--err); color: #000; padding: 6px 12px; border-radius: 4px;
         animation: fadeOut 3s forwards; }
@keyframes fadeOut { 0% { opacity: 1; } 80% { opacity: 1; } 100% { opacity: 0; } }
```

- [ ] **Step 3: JS**

`web/static/app.js`:
```javascript
"use strict";
const $ = (id) => document.getElementById(id);
let schema = null;

async function init() {
  schema = await (await fetch("/api/schema")).json();
  const sel = $("frameType");
  for (const [type, def] of Object.entries(schema.frames)) {
    const opt = document.createElement("option");
    opt.value = type; opt.textContent = def.label || type;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", renderFields);
  $("buildBtn").addEventListener("click", onBuild);
  $("sendBtn").addEventListener("click", onSend);
  $("sendBuildBtn").addEventListener("click", async () => { await onBuild(); await onSend(); });
  renderFields();
  startSSE();
  $("status").textContent = "live"; $("status").classList.add("ok");
}

function renderFields() {
  const t = $("frameType").value;
  const def = schema.frames[t];
  const root = $("fields"); root.innerHTML = "";
  if (!def) return;
  for (const f of def.fields) {
    const lbl = document.createElement("label");
    lbl.innerHTML = `<span>${f.name}${f.unit ? " ("+f.unit+")" : ""}</span>`;
    let inp;
    if (f.type.startsWith && f.type.startsWith("enum{")) {
      inp = document.createElement("select");
      const opts = f.type.slice(5, -1).split(",").map(s => s.trim());
      const labels = f.labels || opts;
      for (let i = 0; i < opts.length; i++) {
        const o = document.createElement("option");
        o.value = opts[i]; o.textContent = labels[i] || opts[i];
        inp.appendChild(o);
      }
    } else if (f.type.startsWith("list[")) {
      inp = document.createElement("input"); inp.type = "text";
      inp.placeholder = "逗号或空格分隔";
    } else { inp = document.createElement("input"); inp.type = "text"; }
    inp.id = "f_" + f.name;
    if (f.default !== undefined) inp.value = JSON.stringify(f.default);
    lbl.appendChild(inp); root.appendChild(lbl);
  }
}

function collectFields() {
  const t = $("frameType").value;
  const def = schema.frames[t]; const out = {};
  for (const f of def.fields) {
    const v = $("f_" + f.name).value.trim();
    if (f.type === "int") out[f.name] = parseInt(v, 10);
    else if (f.type === "float") out[f.name] = parseFloat(v);
    else if (f.type.startsWith("list[")) out[f.name] = v.split(/[,\s]+/).filter(Boolean).map(Number);
    else out[f.name] = v;
  }
  return out;
}

async function onBuild() {
  const t = $("frameType").value; const fields = collectFields();
  const r = await fetch("/api/build", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({type: t, fields})});
  const j = await r.json();
  if (!r.ok) { toast(j.error || "build failed"); return; }
  $("hexOut").value = j.hex; showFrame(j.hex, j.parsed);
}

async function onSend() {
  const hex = $("hexOut").value.trim().replace(/\\s+/g, "");
  const r = await fetch("/api/send", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({target: $("sendTarget").value, hex})});
  const j = await r.json(); if (!r.ok) toast(j.error || "send failed");
}

function startSSE() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.kind === "frame_in" || ev.kind === "frame_out") {
      addLog(ev); showFrame(ev.data.raw_hex, ev.data.parsed);
    } else if (ev.kind === "state") updateStateBar(ev.data);
    else if (ev.kind === "error") toast(ev.data.msg);
    else if (ev.kind === "log") addLog({ts: ev.ts, kind: "log", src: ev.src, data: ev.data});
  };
  es.onerror = () => { $("status").textContent = "offline"; $("status").classList.remove("ok"); };
}

function showFrame(hex, parsed) {
  const bytes = []; const s = hex.replace(/\\s+/g, "");
  for (let i = 0; i < s.length; i += 2) bytes.push(parseInt(s.slice(i, i+2), 16));
  const grid = $("hexGrid"); grid.innerHTML = "";
  for (const b of bytes) {
    const c = document.createElement("div"); c.className = "hex-cell";
    c.textContent = b.toString(16).padStart(2, "0").toUpperCase();
    grid.appendChild(c);
  }
  let txt = "type: " + (parsed && parsed.type) + "\\n";
  if (parsed && parsed.fields) for (const [k, v] of Object.entries(parsed.fields)) txt += `  ${k}: ${JSON.stringify(v)}\\n`;
  $("parsed").textContent = txt;
}

function addLog(ev) {
  const li = document.createElement("li");
  const dir = ev.kind === "frame_in" ? "in" : ev.kind === "frame_out" ? "out" :
              ev.kind === "error" ? "err" : "log";
  li.className = dir;
  const t = new Date(ev.ts * 1000).toLocaleTimeString();
  li.textContent = `${t}  ${ev.kind}  ${ev.src}  ${ev.data && ev.data.parsed ? ev.data.parsed.type : (ev.data && ev.data.msg || "")}`;
  const log = $("log"); log.insertBefore(li, log.firstChild);
  while (log.children.length > 200) log.removeChild(log.lastChild);
}

function updateStateBar(d) {
  const j = (d.joints_rad || []).map(v => v.toFixed(2)).join(" ");
  const t = (d.tcp || []).map(v => v.toFixed(1)).join(" ");
  $("state").textContent = `STATE  J=${j}  TCP=${t}  ${d.is_moving ? "moving" : "idle"}`;
}

function toast(msg) {
  const d = document.createElement("div"); d.className = "toast"; d.textContent = msg;
  $("toasts").appendChild(d); setTimeout(() => d.remove(), 3000);
}

init();
```

- [ ] **Step 4: Manual smoke test**

```bash
python web/run.py --protocol neurapy --auto-start-camera --port 18767 &
PID=$!
sleep 0.5
echo "open http://127.0.0.1:18767 in browser to verify"
kill $PID 2>/dev/null; wait 2>/dev/null
```

- [ ] **Step 5: Commit**

```bash
git add web/static/
git commit -m "feat(web): frontend (HTML + JS + CSS) — schema-driven dynamic form"
```

---

### Task 14: Cross-platform script + manual test doc

**Files:**
- Create: `scripts/check_platform.sh`
- Create: `docs/manual-test.md`

- [ ] **Step 1: Cross-platform check script**

`scripts/check_platform.sh`:
```bash
#!/usr/bin/env bash
# Cross-platform smoke check: forbid non-portable APIs in web/.
set -e
PATTERNS='os\.fork|signal\.SIGWINCH|/proc/|fcntl\.'
DIRS="web/"
fail=0
for d in $DIRS; do
  [ -d "$d" ] || continue
  if grep -rE "$PATTERNS" "$d" 2>/dev/null; then
    echo "FAIL: non-portable API found in $d" >&2; fail=1
  fi
done
[ $fail -eq 0 ] && echo "OK: no non-portable APIs in $DIRS"
exit $fail
```

- [ ] **Step 2: Manual test doc**

`docs/manual-test.md`:
```markdown
# NeuraPY Debug UI — Manual Test Checklist

## Standalone UI
- [ ] `python web/run.py --protocol neurapy --auto-start-camera --port 8765`
- [ ] Browser: http://127.0.0.1:8765 shows 3 panels
- [ ] Frame type dropdown lists: query, motion, status
- [ ] Build + Send round-trips: hex in textarea, log shows frame_out + frame_in
- [ ] Stop fake_camera via /api/disconnect, log shows disconnect

## Full closed loop
- [ ] `python web/run_debug.py --protocol neurapy` (Linux + neurapy)
- [ ] log shows connect (point_client -> fake_camera)
- [ ] Build + Send -> state bar updates
- [ ] Stop point_client (Ctrl-C), run_debug logs "exited N; restarting"

## Cross-platform
- [ ] `python -m unittest discover -s web/tests` green on macOS / Windows / Linux
- [ ] `python -m unittest test_binary.py` still green
- [ ] `bash scripts/check_platform.sh` reports OK

## New protocol flow
- [ ] `cp web/protocols/_template.py /tmp/my.py`
- [ ] Edit FRAME_SIZE + 4 methods
- [ ] `python web/run.py --protocol /tmp/my.py:MyProtocol` starts without error
```

- [ ] **Step 3: Run check script**

```bash
chmod +x scripts/check_platform.sh
bash scripts/check_platform.sh
```
Expected: `OK: no non-portable APIs in web/`

- [ ] **Step 4: Commit**

```bash
git add scripts/check_platform.sh docs/manual-test.md
git commit -m "docs: add cross-platform check script + manual test checklist"
```

---

### Task 15: README update

**Files:**
- Modify: `README.md` (append "Debug UI" section)

- [ ] **Step 1: Append section**

```markdown
## Debug UI (optional)

Web-based debug panel for the 96-byte binary protocol. Lives in `web/`. Zero new pip dependencies; macOS / Windows / Linux (Python 3.7+).

### Run
```bash
# Standalone UI + fake camera (no real point_client)
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Debug UI section to README"
```

---

## Final Verification

After all 15 tasks:
- [ ] `python -m unittest discover -s web/tests` — all green
- [ ] `python -m unittest test_binary.py` — still green
- [ ] `bash scripts/check_platform.sh` — OK
- [ ] `python web/run.py --protocol neurapy --auto-start-camera` — starts cleanly
- [ ] Browser smoke test per `docs/manual-test.md` passes

## Notes for the Implementer

- **Do not modify** `point_client.py` / `vision_protocol.py` / `parse_frame.py` / `build_frame.py` / `test_binary.py` (spec Out).
- **PYTHONPATH injection** is the contract: `web/mock/neurapy/robot.py` must satisfy `from neurapy.robot import Robot`. Task 11 prepends `web/mock/` to `PYTHONPATH`.
- **Test isolation**: each test uses `_free_port()` helper to grab a random local port. Never bind to fixed ports (tests would flake on parallel runs).
- **SSE backpressure**: each subscriber's queue is `maxsize=2000`; if the consumer (browser) lags, oldest events are dropped silently.
- **Threading quirks**: `ThreadingHTTPServer` handles concurrent requests, but `BaseHTTPRequestHandler` instances are NOT shared. `FakeCamera` state is accessed via `_Handler.cam` class attribute.
- **State injection in run.py**: `_State` is created inside `make_server`; `run.py` and `run_debug.py` set `srv._state.fake_camera` after construction. If a future test needs more state fields, refactor Task 9 to return `(server, state)` tuple.
