#!/usr/bin/env python3
"""Webhook-to-image server for the Stock Ticker's custom image page.

The reader sleeps most of the time, so it cannot receive webhooks itself. This
server receives them, keeps the latest one, and renders it as a 1-bit image the
reader pulls on each wake. Payload format: docs/webhook-format.md.

    POST /webhook        JSON object (see docs/webhook-format.md). Replaces the current content.
    GET  /dashboard.png  The rendered image. ?w=&h= picks the size (default 792x528,
                         X3 landscape; use w=528&h=792 for portrait).
    GET  /dashboard.bmp  Same, as a 1-bit BMP.
    GET  /               Status: count, time and the stored payload.

Usage: webhook_server.py [--port 8080] [--out DIR]   (Pillow required)
Reader setting: Custom image URL = http://<this-computer>:8080/dashboard.png?w=792&h=528
"""
import argparse
import base64
import io
import json
import os
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "C:/Windows/Fonts"]
MAX_BODY = 12 * 1024 * 1024  # base64 images included
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 64, 2048
DEFAULT_W, DEFAULT_H = 792, 528
MAX_TEXT_LINES = 6

LOCK = threading.Lock()
STATE = {"payload": {"title": "Waiting for a webhook", "text": "POST JSON to /webhook"}, "received": None, "count": 0}
# Images are loaded once per webhook (keyed by webhook count), so a camera URL that
# returns a new snapshot is fetched again for the next webhook instead of going stale.
IMAGE_CACHE = {}


def font(size, bold=False):
    names = ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"]
    for d in FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---- request parsing ------------------------------------------------------------


def parse_body(raw):
    """Webhook body -> payload dict. Non-objects become {"value": ...}; non-JSON becomes {"text": ...}."""
    try:
        payload = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"text": raw.decode("utf-8", "replace").strip()}
    return payload if isinstance(payload, dict) else {"value": payload}


def parse_size(query):
    """?w=&h= -> (w, h), or None if not integers within MIN_SIDE..MAX_SIDE."""
    try:
        w = int(query.get("w", [DEFAULT_W])[0])
        h = int(query.get("h", [DEFAULT_H])[0])
    except ValueError:
        return None
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        return None
    return w, h


def normalize_items(items):
    """items -> [(label, value)]. Accepts {"label","value"} objects, [label, value] pairs or plain strings."""
    rows = []
    if not isinstance(items, list):
        return rows
    for it in items:
        if isinstance(it, dict):
            rows.append((str(it.get("label", "")), str(it.get("value", ""))))
        elif isinstance(it, (list, tuple)) and it:
            rows.append((str(it[0]), str(it[1]) if len(it) > 1 else ""))
        else:
            rows.append((str(it), ""))
    return rows


def chart_values(chart):
    """Numbers only (booleans excluded); None unless at least two remain."""
    if not isinstance(chart, list):
        return None
    pts = [float(v) for v in chart if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return pts if len(pts) >= 2 else None


# ---- images ----------------------------------------------------------------------


def decode_image(data):
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im)
    return im.convert("L")


def load_image(src, key=None):
    """PIL image from an http(s) URL or base64 (optionally a data: URI); None if unusable."""
    cache_key = (key, src)
    if cache_key in IMAGE_CACHE:
        return IMAGE_CACHE[cache_key]
    try:
        if src.startswith(("http://", "https://")):
            with urlopen(Request(src, headers={"User-Agent": "TickerWebhook/1.0"}), timeout=10) as r:
                data = r.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("image larger than 8 MB")
        else:
            data = base64.b64decode(src.split(",", 1)[1] if src.startswith("data:") else src, validate=False)
        im = decode_image(data)
    except Exception as e:  # bad URL, network error, not an image
        print(f"image load failed: {e}", flush=True)
        im = None
    IMAGE_CACHE[cache_key] = im
    return im


def draw_image(canvas, im, box):
    """Fit inside box, then Floyd-Steinberg dither to 1-bit so photos keep their tones on e-ink."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return
    scale = min(bw / im.width, bh / im.height)
    fitted = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    canvas.paste(fitted.convert("1"), (x0 + (bw - fitted.width) // 2, y0 + (bh - fitted.height) // 2))


# ---- drawing -----------------------------------------------------------------------


def fit_text(d, text, fnt, max_w):
    """Truncate with an ellipsis so text never runs past max_w pixels."""
    text = str(text)
    if d.textlength(text, font=fnt) <= max_w:
        return text
    while text and d.textlength(text + "...", font=fnt) > max_w:
        text = text[:-1]
    return text + "..."


def draw_chart(d, pts, box, title=None):
    x0, y0, x1, y1 = box
    if title:
        d.text((x0, y0), fit_text(d, title, font(22, True), x1 - x0), font=font(22, True), fill=0)
        y0 += 32
    d.line((x0, y1, x1, y1), fill=0, width=2)
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    top, bottom = y0 + 26, y1 - 6
    if bottom - top < 10:
        return
    xy = [(x0 + i * (x1 - x0) / (len(pts) - 1), bottom - (p - lo) / span * (bottom - top)) for i, p in enumerate(pts)]
    d.line(xy, fill=0, width=3, joint="curve")
    d.text((x0, y0), f"{hi:g}", font=font(18), fill=0)
    d.text((x0, bottom - 22), f"{lo:g}", font=font(18), fill=0)
    last = f"{pts[-1]:g}"
    d.text((x1 - d.textlength(last, font=font(20, True)), max(y0, xy[-1][1] - 30)), last, font=font(20, True), fill=0)


def render_payload(payload, w, h, received=None, count=0, image_loader=load_image):
    """Pure renderer: payload dict -> 1-bit PIL image of size (w, h)."""
    p = dict(payload)
    im = Image.new("1", (w, h), 1)  # 1 = white
    d = ImageDraw.Draw(im)
    m = 28
    footer_h = 40
    footer_top = h - footer_h

    title = p.pop("title", None)
    title = "Webhook" if title in (None, "") else str(title)
    d.text((m, m), fit_text(d, title, font(44, True), w - 2 * m), font=font(44, True), fill=0)
    y = m + 62
    d.line((m, y, w - m, y), fill=0, width=3)
    y += 20

    src = p.pop("image", None)
    image = image_loader(str(src), count) if src else None
    pts = chart_values(p.pop("chart", None))
    chart_title = p.pop("chart_title", None)
    footer = p.pop("footer", None)
    text = p.pop("text", None)
    rows = normalize_items(p.pop("items", None))
    rows += [(str(k), json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in p.items()]
    has_text = bool(text) or bool(rows)

    # Graphics: right column in landscape, lower half in portrait, everything without text.
    text_right, text_bottom = w - m, footer_top - 12
    if image is not None or pts is not None:
        if not has_text:
            gbox = (m, y, w - m, footer_top - 12)
        elif w >= h:
            gx0 = int(w * 0.48)
            gbox = (gx0, y, w - m, footer_top - 12)
            text_right = gx0 - 20
        else:
            gy0 = y + int((footer_top - y) * 0.45)
            gbox = (m, gy0, w - m, footer_top - 12)
            text_bottom = gy0 - 12
        if image is not None and pts is not None:
            mid = (gbox[1] + gbox[3]) // 2
            draw_image(im, image, (gbox[0], gbox[1], gbox[2], mid - 8))
            draw_chart(d, pts, (gbox[0], mid + 8, gbox[2], gbox[3]), chart_title)
        elif image is not None:
            draw_image(im, image, gbox)
        else:
            draw_chart(d, pts, gbox, chart_title)

    if text:
        chars = max(10, (text_right - m) // 17)
        for line in textwrap.wrap(str(text), chars)[:MAX_TEXT_LINES]:
            if y + 40 > text_bottom:
                break
            d.text((m, y), fit_text(d, line, font(30), text_right - m), font=font(30), fill=0)
            y += 40
        y += 10

    for label, value in rows:
        if y + 36 > text_bottom:
            break
        vfont = font(26, True)
        value = fit_text(d, value, vfont, (text_right - m) * 0.6)
        vw = d.textlength(value, font=vfont)
        d.text((m, y), fit_text(d, label, font(26), text_right - m - vw - 12), font=font(26), fill=0)
        d.text((text_right - vw, y), value, font=vfont, fill=0)
        y += 36
        d.line((m, y, text_right, y), fill=0, width=1)
        y += 10

    if footer is not None:
        footer_text = str(footer)
    elif received:
        footer_text = f"Webhook #{count}, received {time.strftime('%H:%M:%S', time.localtime(received))}"
    else:
        footer_text = "No webhook received yet"
    d.rectangle((0, footer_top, w, h), fill=0)
    d.text((m, footer_top + 8), fit_text(d, footer_text, font(20, True), w - 2 * m), font=font(20, True), fill=1)
    return im


def render(w, h):
    with LOCK:
        payload, received, count = dict(STATE["payload"]), STATE["received"], STATE["count"]
    try:
        return render_payload(payload, w, h, received, count)
    except Exception as e:  # never let one bad webhook break every image request
        print(f"render failed: {e}", flush=True)
        return render_payload({"title": "Render error", "text": str(e)}, w, h, received, count)


def store(payload):
    with LOCK:
        STATE["payload"], STATE["received"] = payload, time.time()
        STATE["count"] += 1
        IMAGE_CACHE.clear()
        return STATE["count"]


# ---- HTTP ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "TickerWebhook/1.0"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode() + b"\n", "application/json")

    def do_POST(self):
        if urlparse(self.path).path != "/webhook":
            return self._json(404, {"ok": False, "error": "not found"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            return self._json(413, {"ok": False, "error": f"body larger than {MAX_BODY} bytes"})
        n = store(parse_body(self.rfile.read(length)))
        self._json(200, {"ok": True, "count": n})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/dashboard.png", "/dashboard.bmp"):
            size = parse_size(parse_qs(u.query))
            if size is None:
                return self._json(400, {"ok": False, "error": f"w and h must be integers {MIN_SIDE}-{MAX_SIDE}"})
            im = render(*size)
            fmt = "PNG" if u.path.endswith(".png") else "BMP"
            buf = io.BytesIO()
            im.save(buf, fmt)
            out_dir = getattr(self.server, "out_dir", None)
            if out_dir:
                im.save(os.path.join(out_dir, "last_served.png"))
            return self._send(200, buf.getvalue(), "image/png" if fmt == "PNG" else "image/bmp")
        if u.path == "/":
            with LOCK:
                info = {"count": STATE["count"], "received": STATE["received"], "payload": STATE["payload"]}
            return self._send(200, json.dumps(info, indent=2).encode() + b"\n", "application/json")
        self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        print(f"{time.strftime('%H:%M:%S')} {self.client_address[0]} {fmt % args}", flush=True)


def make_server(port, out_dir=None, host="0.0.0.0"):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.out_dir = out_dir
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--out", default=None, help="also save each served image here as last_served.png")
    a = ap.parse_args()
    srv = make_server(a.port, a.out)
    print(f"listening on :{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
