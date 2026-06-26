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
