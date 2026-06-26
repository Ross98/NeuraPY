"""SSE (Server-Sent Events) serialization."""
import json


def sse_format(event: dict) -> bytes:
    """Serialize event dict as one SSE frame: b'data: <json>\\n\\n'.

    Uses ensure_ascii=True so multi-byte chars become \\uXXXX, avoiding
    any encoding ambiguity on the wire (browsers parse this as UTF-8).
    """
    payload = json.dumps(event, ensure_ascii=True)
    return f"data: {payload}\n\n".encode("ascii")
