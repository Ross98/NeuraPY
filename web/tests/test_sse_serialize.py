import unittest
from web.sse import sse_format


class TestSSE(unittest.TestCase):
    def test_basic_event(self):
        out = sse_format({"kind": "x", "data": 1})
        self.assertEqual(out, b'data: {"kind": "x", "data": 1}\n\n')

    def test_chinese_escaped(self):
        out = sse_format({"data": "操控"})
        self.assertIn(b'\\u64cd\\u63a7', out)
        self.assertTrue(out.endswith(b'\n\n'))

    def test_newline_in_data_escaped(self):
        out = sse_format({"data": "line1\nline2"})
        # json.dumps escapes \n as literal \n (2 bytes: 0x5c 0x6e), not newline
        self.assertIn(b'\\n', out)
        # after the "data: " prefix, no raw newline should appear
        body = out[len(b'data: '):]
        # body ends with "\n\n" (the SSE terminator); strip it to check payload
        self.assertTrue(body.endswith(b'\n\n'))
        payload = body[:-2]
        self.assertNotIn(b'\n', payload)

    def test_bytes_returned(self):
        self.assertIsInstance(sse_format({}), bytes)
