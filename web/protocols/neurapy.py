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
        if frame[0:4] == b"\x02\x02\x02\x02":
            return "query"
        if frame[0:4] == b"\x02\x01\x01\x00":
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
            return b"\x02\x02\x02\x02" + b"\x00" * 92
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
                "query": {"label": "\u67e5\u8be2\u5e27", "fields": []},
                "motion": {
                    "label": "\u8fd0\u52a8\u63a7\u5236\u5e27",
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
                    "label": "\u72b6\u6001\u5e27",
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
