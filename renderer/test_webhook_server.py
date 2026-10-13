#!/usr/bin/env python3
"""Unit tests for webhook_server.py. Run: python3 -m unittest -v tools/stock-ticker/test_webhook_server.py"""
import base64
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webhook_server as ws  # noqa: E402

W, H = 792, 528


def png_bytes(size=(60, 40), color=0, mode="L"):
    im = Image.new(mode, size, color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def ink(im, box):
    """Number of black pixels inside box (x0, y0, x1, y1)."""
    return im.convert("L").crop(box).histogram()[0]


class ParseTests(unittest.TestCase):
    def test_object(self):
        self.assertEqual(ws.parse_body(b'{"title": "a"}'), {"title": "a"})

    def test_empty_body(self):
        self.assertEqual(ws.parse_body(b""), {})

    def test_non_object_json_becomes_value(self):
        self.assertEqual(ws.parse_body(b"[1, 2]"), {"value": [1, 2]})
        self.assertEqual(ws.parse_body(b"42"), {"value": 42})

    def test_plain_text_becomes_text(self):
        self.assertEqual(ws.parse_body(b"door opened\n"), {"text": "door opened"})

    def test_invalid_utf8_does_not_raise(self):
        self.assertIn("text", ws.parse_body(b"\xff\xfe bad"))

    def test_size(self):
        self.assertEqual(ws.parse_size({}), (792, 528))
        self.assertEqual(ws.parse_size({"w": ["528"], "h": ["792"]}), (528, 792))
        self.assertIsNone(ws.parse_size({"w": ["abc"]}))
        self.assertIsNone(ws.parse_size({"w": ["10"]}))
        self.assertIsNone(ws.parse_size({"h": ["99999"]}))

    def test_items_forms(self):
        rows = ws.normalize_items([{"label": "A", "value": 1}, ["B", 2], ["C"], "D", 5])
        self.assertEqual(rows, [("A", "1"), ("B", "2"), ("C", ""), ("D", ""), ("5", "")])
        self.assertEqual(ws.normalize_items("not a list"), [])

    def test_chart_values(self):
        self.assertEqual(ws.chart_values([1, 2.5, "x", None, True, 3]), [1.0, 2.5, 3.0])
        self.assertIsNone(ws.chart_values([1]))
        self.assertIsNone(ws.chart_values("1,2,3"))


class RenderTests(unittest.TestCase):
    def render(self, payload, w=W, h=H, loader=None):
        return ws.render_payload(payload, w, h, received=1_700_000_000, count=3,
                                 image_loader=loader or (lambda src, key: None))

    def test_size_and_mode(self):
        for w, h in ((792, 528), (528, 792)):
            im = self.render({"title": "x"}, w, h)
            self.assertEqual(im.size, (w, h))
            self.assertEqual(im.mode, "1")

    def test_deterministic(self):
        payload = {"title": "Same", "text": "input", "chart": [1, 3, 2]}
        self.assertEqual(self.render(payload).tobytes(), self.render(payload).tobytes())

    def test_title_and_footer_are_drawn(self):
        im = self.render({"title": "Hello"})
        self.assertGreater(ink(im, (28, 28, 400, 80)), 100)  # title
        self.assertGreater(ink(im, (0, H - 40, W, H)), W * 30)  # black footer bar

    def test_blank_title_falls_back(self):
        self.assertGreater(ink(self.render({"title": ""}), (28, 28, 300, 80)), 50)

    def test_footer_override(self):
        a = self.render({"title": "x"})
        b = self.render({"title": "x", "footer": "Updated by Home Assistant"})
        self.assertNotEqual(a.crop((0, H - 40, W, H)).tobytes(), b.crop((0, H - 40, W, H)).tobytes())

    def test_graphics_go_right_in_landscape(self):
        im = self.render({"title": "t", "text": "x", "chart": [1, 5, 2, 8]})
        self.assertGreater(ink(im, (int(W * 0.5), 120, W - 28, H - 60)), 200)

    def test_graphics_go_below_text_in_portrait(self):
        w, h = 528, 792
        im = self.render({"title": "t", "text": "short", "chart": [1, 5, 2, 8]}, w, h)
        self.assertGreater(ink(im, (28, int(h * 0.5), w - 28, h - 60)), 200)

    def test_graphics_use_full_width_without_text(self):
        im = self.render({"title": "t", "chart": [1, 5, 2, 8]})
        self.assertGreater(ink(im, (28, 140, int(W * 0.4), H - 60)), 100)  # left half used too

    def test_image_is_drawn(self):
        black = Image.new("L", (100, 100), 0)
        im = self.render({"title": "t", "image": "x"}, loader=lambda src, key: black)
        self.assertGreater(ink(im, (100, 120, W - 100, H - 60)), 5000)

    def test_unusable_image_is_ignored(self):
        im = self.render({"title": "t", "image": "x", "text": "still here"})
        self.assertEqual(im.size, (W, H))

    def test_long_values_do_not_overflow(self):
        im = self.render({"title": "T" * 300, "items": [{"label": "L" * 200, "value": "V" * 200}]})
        self.assertEqual(ink(im, (W - 27, 0, W, H - 40)), 0)  # right margin stays blank

    def test_many_rows_stop_before_footer(self):
        im = self.render({"title": "t", "items": [[f"row {i}", i] for i in range(50)]})
        self.assertEqual(ink(im, (0, H - 52, W, H - 41)), 0)  # gap above the footer stays blank

    def test_hostile_payload_does_not_raise(self):
        self.render({"title": None, "text": 123, "items": [None, {}, [[]]], "chart": [{}, "1"],
                     "nested": {"a": [1, 2]}, "image": ""})


class ImageLoaderTests(unittest.TestCase):
    def setUp(self):
        ws.IMAGE_CACHE.clear()

    def test_data_uri_and_raw_base64(self):
        b64 = base64.b64encode(png_bytes()).decode()
        self.assertEqual(ws.load_image("data:image/png;base64," + b64).size, (60, 40))
        self.assertEqual(ws.load_image(b64, key=2).size, (60, 40))

    def test_transparency_becomes_white(self):
        rgba = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        buf = io.BytesIO()
        rgba.save(buf, "PNG")
        im = ws.load_image(base64.b64encode(buf.getvalue()).decode())
        self.assertEqual(im.getpixel((0, 0)), 255)

    def test_garbage_returns_none(self):
        self.assertIsNone(ws.load_image("data:image/png;base64,bm90IGFuIGltYWdl"))
        self.assertIsNone(ws.load_image("http://127.0.0.1:9/nothing.png"))


class _ImageHandler(BaseHTTPRequestHandler):
    color = [0]

    def do_GET(self):
        body = png_bytes(color=self.color[0])
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ws.Handler.log_message = lambda *a: None
        cls.srv = ws.make_server(0, host="127.0.0.1")
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.img = ThreadingHTTPServer(("127.0.0.1", 0), _ImageHandler)
        cls.img_url = f"http://127.0.0.1:{cls.img.server_address[1]}/cam.png"
        threading.Thread(target=cls.img.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.srv, cls.img):
            srv.shutdown()
            srv.server_close()

    def post(self, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        with urlopen(Request(self.base + "/webhook", data=data, headers={"Content-Type": "application/json"})) as r:
            return json.load(r)

    def get(self, path):
        with urlopen(self.base + path) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def test_post_then_png(self):
        before = self.post({"title": "Build", "text": "passed"})["count"]
        self.assertEqual(self.post({"title": "Build 2"})["count"], before + 1)
        status, ctype, body = self.get("/dashboard.png?w=528&h=792")
        self.assertEqual((status, ctype), (200, "image/png"))
        im = Image.open(io.BytesIO(body))
        self.assertEqual((im.size, im.mode), ((528, 792), "1"))

    def test_bmp(self):
        status, ctype, body = self.get("/dashboard.bmp")
        self.assertEqual((status, ctype), (200, "image/bmp"))
        self.assertEqual(body[:2], b"BM")

    def test_status(self):
        self.post({"title": "Status check"})
        _, ctype, body = self.get("/")
        self.assertEqual(json.loads(body)["payload"]["title"], "Status check")

    def test_bad_size_is_400(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/dashboard.png?w=abc")
        self.assertEqual(cm.exception.code, 400)

    def test_unknown_path_is_404(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_oversized_body_is_413(self):
        req = Request(self.base + "/webhook", data=b"{}", headers={"Content-Length": str(ws.MAX_BODY + 1)})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req)
        self.assertEqual(cm.exception.code, 413)

    def test_bad_items_do_not_break_rendering(self):
        self.post({"title": "x", "items": ["text", 5, None]})
        self.assertEqual(self.get("/dashboard.png")[0], 200)

    def test_same_image_url_is_refetched_for_each_webhook(self):
        _ImageHandler.color[0] = 0  # black snapshot
        self.post({"title": "cam", "image": self.img_url})
        dark = Image.open(io.BytesIO(self.get("/dashboard.png")[2]))
        _ImageHandler.color[0] = 255  # camera now returns a white snapshot
        self.post({"title": "cam", "image": self.img_url})
        light = Image.open(io.BytesIO(self.get("/dashboard.png")[2]))
        self.assertGreater(ink(dark, (100, 100, 700, 480)), ink(light, (100, 100, 700, 480)) + 5000)


if __name__ == "__main__":
    unittest.main()
