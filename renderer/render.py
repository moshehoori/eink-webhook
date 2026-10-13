#!/usr/bin/env python3
"""Render one webhook payload to the e-ink images and publishable files.

Usage: render.py payload.json OUT_DIR

Writes dashboard.png (792x528, landscape), dashboard-portrait.png (528x792),
payload.json (the payload as received) and status.json (time and render number).
Rendering is webhook_server.render_payload, the same code the local webhook
server uses, so both produce identical images. Footer times use the TZ env var.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webhook_server as ws  # noqa: E402

SIZES = {"dashboard.png": (792, 528), "dashboard-portrait.png": (528, 792)}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        payload = ws.parse_body(f.read())
    os.makedirs(out, exist_ok=True)
    received = time.time()
    count = int(os.environ.get("RUN_NUMBER", "0") or 0)
    for name, (w, h) in SIZES.items():
        ws.render_payload(payload, w, h, received, count).save(os.path.join(out, name))
    with open(os.path.join(out, "payload.json"), "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "status.json"), "w") as f:
        json.dump({"render": count, "received": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(received))}, f)
    print(f"rendered #{count}: {', '.join(SIZES)}")


if __name__ == "__main__":
    main()
