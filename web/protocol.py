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
    def parse(self, frame: bytes, expected_type: str = None) -> Dict[str, Any]:
        """Return {"type": "<classify result>", "fields": {<name>: <value>, ...}}.

        `expected_type` is an optional hint for protocols (like NeuraPY)
        where two frame types share the same header and the decoder
        cannot disambiguate by header alone. Callers that know the
        frame's origin (e.g. they just built it, or it's a known
        response) should pass the type so parsing is unambiguous.
        """

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
