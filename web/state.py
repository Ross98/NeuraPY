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
