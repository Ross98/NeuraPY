import os
import tempfile
import unittest
from web.protocol import Protocol
from web.protocols import REGISTRY, load


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
        tmpl = (
            "from web.protocol import Protocol\n"
            "class TmpProto(Protocol):\n"
            "    FRAME_SIZE = 8\n"
            "    def classify(self, frame): return 'x'\n"
            "    def parse(self, frame): return {'type': 'x', 'fields': {}}\n"
            "    def build(self, type, **f): return b'\\x00' * 8\n"
            "    @property\n"
            "    def schema(self): return {'frames': {}}\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(tmpl)
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
