"""Protocol abstraction: the one place project-specific knowledge lives."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class Protocol(ABC):
    """All debug-UI protocol adapters implement this interface.

    The UI loads one Protocol instance at startup via --protocol.
    fake_camera / inspector / server.py all delegate frame work to it.
    """

    FRAME_SIZE: int  # subclass must set

    @abstractmethod
    def classify(self, frame: bytes) -> str:
        """Return frame type name. e.g. 'query' / 'motion' / 'status' / 'unknown'."""

    @abstractmethod
    def parse(self, frame: bytes) -> Dict[str, Any]:
        """Return {"type": "<classify result>", "fields": {<name>: <value>, ...}}."""

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
