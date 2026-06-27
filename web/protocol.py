"""Protocol abstraction: the one place project-specific knowledge lives."""
from abc import ABC, abstractmethod
from typing import Any, Dict

# Valid values for the optional frame `direction` field in schema.
FRAME_DIRECTIONS = frozenset({"camera_to_robot", "robot_to_camera"})


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
        """Return UI form schema: {"frames": {<type>: {...}}}.

        Each frame entry should (when applicable) carry a `direction`
        field with one of:
          - "camera_to_robot"  (e.g. motion / query frames)
          - "robot_to_camera"  (e.g. status frames)
        The frontend uses this to color frames by origin and restrict
        which send-target is valid. The field is OPTIONAL — protocols
        without bidirectional traffic (e.g. a one-way sensor protocol)
        can omit it.

        Field metadata: name, type, unit?, offset, length, default?.
        Types: int | float | bytes | list[int] | list[float] | list[float3] | list[float6] | enum{...}
        """
