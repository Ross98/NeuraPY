"""NeuraPY Protocol adapter. vision_protocol.py is untouched (spec Out)."""
from typing import Any, Dict

from vision_protocol import VisionProtocol

from web.protocol import Protocol


class NeuraPYProtocol(Protocol):
    FRAME_SIZE = 96

    # Frame-type tags returned by parse() when header matches 02 01 01 00
    # but motion vs status cannot be distinguished by header alone.
    MOTION_OR_STATUS = "motion_or_status"

    def __init__(self):
        self._vp = VisionProtocol()

    def classify(self, frame: bytes) -> str:
        if len(frame) < 4:
            return "unknown"
        if frame[0:4] == b"\x02\x02\x02\x02":
            return "query"
        if frame[0:4] == b"\x02\x01\x01\x00":
            # Both motion (camera→robot) and status (robot→camera) frames
            # use this header. The caller must use parse(frame, expected_type=...)
            # to get the correct fields.
            return self.MOTION_OR_STATUS
        return "unknown"

    def parse(self, frame: bytes, expected_type: str = None) -> Dict[str, Any]:
        """Decode a 96-byte frame.

        Args:
            frame: raw bytes (must be FRAME_SIZE).
            expected_type: optional "motion" / "status" hint. The motion
                and status frames share the same header AND the same
                payload byte offsets for fields 4-51; without a hint, the
                decoder can't reliably tell which is which and returns
                raw fields from both layouts. Pass expected_type when
                you know the frame's origin (e.g. you built it yourself,
                or it came from the inspector with direction metadata).
        """
        if len(frame) != self.FRAME_SIZE:
            raise ValueError(f"frame must be {self.FRAME_SIZE} bytes, got {len(frame)}")
        t = self.classify(frame)
        if t == "query":
            return {"type": "query", "fields": {}}
        if expected_type == "motion":
            return {"type": "motion", "fields": self._flatten(self._vp.parse_motion(frame))}
        if expected_type == "status":
            return {"type": "status", "fields": self._flatten(self._vp.parse_status(frame))}
        # No hint — return merged fields from both parsers so the caller
        # can disambiguate. This is intentionally lossy when both
        # interpretations are valid; the inspector will label it
        # "motion_or_status" and the UI can pick.
        m = self._vp.parse_motion(frame)
        s = self._vp.parse_status(frame)
        return {"type": self.MOTION_OR_STATUS,
                "fields": {"motion": self._flatten(m), "status": self._flatten(s)}}

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
                request_motion=fields.get("request_motion", 1),
                enter_area=bytes(fields.get("enter_area") or b""),
                exit_area=bytes(fields.get("exit_area") or b""),
            )
        if type == "status":
            flags = fields.get("flags", {}) or {}
            def _f(name, default=0):
                if name in fields:
                    return fields[name]
                return flags.get(name, default)
            return self._vp.build_status(
                joints=fields.get("joints", [0, 0, 0, 0, 0, 0]),
                position=fields.get("position", [0, 0, 0]),
                orientation=fields.get("orientation", [0, 0, 0]),
                work_status=_f("work_status"),
                at_origin=_f("at_origin"),
                emergency_stop=_f("emergency_stop"),
                is_moving=_f("is_moving"),
                main_program_started=_f("main_program_started", 1),
                work_area=_f("work_area"),
                exception=_f("exception"),
                exception_code=_f("exception_code"),
            )
        raise ValueError(f"unknown frame type: {type!r}")

    @property
    def schema(self) -> Dict[str, Any]:
        """UI schema.

        Each frame entry carries a `direction` (camera_to_robot /
        robot_to_camera) so the frontend can color frames by origin and
        restrict send-targets accordingly. Fields are listed in offset
        order to help the UI render the byte layout visually.
        """
        return {
            "frames": {
                "query": {
                    "label": "查询帧",
                    "direction": "camera_to_robot",
                    "fields": [],
                },
                "motion": {
                    "label": "运动控制帧",
                    "direction": "camera_to_robot",
                    "fields": [
                        {"name": "joints", "type": "list[float6]", "unit": "deg",
                         "offset": 4, "length": 24, "default": [0, 0, 0, 0, 0, 0]},
                        {"name": "position", "type": "list[float3]", "unit": "mm",
                         "offset": 28, "length": 12, "default": [0, 0, 0]},
                        {"name": "orientation", "type": "list[float3]", "unit": "deg",
                         "offset": 40, "length": 12, "default": [0, 0, 0]},
                        {"name": "work_area", "type": "int",
                         "offset": 52, "length": 1, "default": 0},
                        {"name": "speed", "type": "int",
                         "offset": 53, "length": 1, "default": 5},
                        {"name": "blend_radius", "type": "int",
                         "offset": 54, "length": 1, "default": 0},
                        {"name": "motion_type", "type": "enum{1,2,3}",
                         "offset": 55, "length": 1, "default": 1,
                         "labels": ["MoveAbsJ", "MoveJ", "MoveL"]},
                        {"name": "request_motion", "type": "enum{0,1}",
                         "offset": 56, "length": 1, "default": 1},
                        {"name": "point_id", "type": "int",
                         "offset": 57, "length": 4, "default": 1},
                        {"name": "enter_area", "type": "bytes",
                         "offset": 61, "length": 16, "default": ""},
                        {"name": "exit_area", "type": "bytes",
                         "offset": 77, "length": 16, "default": ""},
                    ],
                },
                "status": {
                    "label": "状态帧",
                    "direction": "robot_to_camera",
                    "fields": [
                        {"name": "joints", "type": "list[float6]", "unit": "deg",
                         "offset": 4, "length": 24},
                        {"name": "position", "type": "list[float3]", "unit": "mm",
                         "offset": 28, "length": 12},
                        {"name": "orientation", "type": "list[float3]", "unit": "deg",
                         "offset": 40, "length": 12},
                        {"name": "work_status", "type": "enum{0,1}",
                         "offset": 52, "length": 1, "default": 0},
                        {"name": "at_origin", "type": "enum{0,1}",
                         "offset": 53, "length": 1, "default": 0},
                        {"name": "emergency_stop", "type": "enum{0,1}",
                         "offset": 54, "length": 1, "default": 0},
                        {"name": "is_moving", "type": "enum{0,1}",
                         "offset": 55, "length": 1, "default": 0},
                        {"name": "main_program_started", "type": "enum{0,1}",
                         "offset": 56, "length": 1, "default": 1},
                        {"name": "work_area", "type": "int",
                         "offset": 57, "length": 1, "default": 0},
                        {"name": "exception", "type": "enum{0,1}",
                         "offset": 58, "length": 1, "default": 0},
                        {"name": "exception_code", "type": "int",
                         "offset": 59, "length": 1, "default": 0},
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