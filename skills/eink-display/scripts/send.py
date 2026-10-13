#!/usr/bin/env python3
"""Send a webhook to the e-ink display (GitHub repository_dispatch -> Actions renders it).

Examples:
  send.py --title "Build passed" --item "Branch=main" --item "Tests=1,204" --footer "CI 14:32"
  send.py --title "Living room" --item "Temp=23.4 °C" --chart 21.8,22.1,22.6,23.4 --chart-title "Temp, 4 h"
  send.py --json '{"title": "Front door", "text": "Package delivered"}'
  send.py --file payload.json          (or "-" for stdin)
  send.py ... --wait                   wait until the image is published, print its URL

Configuration (environment):
  EINK_REPO          owner/repo of the webhook repo (default: moshehoori/eink-webhook)
  EINK_GITHUB_TOKEN  token with Contents read/write on that repo; falls back to
                     GITHUB_TOKEN, then to `gh auth token`
Exit codes: 0 sent (and published with --wait), 1 bad input, 2 GitHub error, 3 wait timed out.
"""
import argparse
import calendar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_REPO = "moshehoori/eink-webhook"
MAX_TOP_LEVEL_KEYS = 10  # GitHub limit for client_payload
MAX_PAYLOAD_BYTES = 60_000  # keep well under GitHub's request limits; prefer image URLs to base64


def token():
    for var in ("EINK_GITHUB_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    # gh >= 2.17 has `gh auth token`; older versions keep it in the config.
    for cmd in (["gh", "auth", "token"], ["gh", "config", "get", "-h", "github.com", "oauth_token"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit("No GitHub token: set EINK_GITHUB_TOKEN (Contents: read & write on the webhook repo) or log in with gh.")


def api(method, url, tok, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "eink-webhook-send",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def build_payload(a):
    if a.json:
        payload = json.loads(a.json)
    elif a.file:
        payload = json.load(sys.stdin if a.file == "-" else open(a.file, encoding="utf-8"))
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    for key in ("title", "text", "image", "footer"):
        if getattr(a, key):
            payload[key] = getattr(a, key)
    if a.chart_title:
        payload["chart_title"] = a.chart_title
    if a.item:
        rows = payload.setdefault("items", [])
        for it in a.item:
            label, _, value = it.partition("=")
            rows.append([label.strip(), value.strip()])
    if a.chart:
        payload["chart"] = [float(v) for v in a.chart.split(",") if v.strip()]
    return payload


def check(payload):
    if not payload:
        sys.exit("Nothing to send: give --title/--text/--item/--chart/--image, --json or --file.")
    if len(payload) > MAX_TOP_LEVEL_KEYS:
        sys.exit(f"GitHub allows at most {MAX_TOP_LEVEL_KEYS} top-level fields; got {len(payload)}. "
                 "Move extra values into \"items\".")
    size = len(json.dumps(payload).encode())
    if size > MAX_PAYLOAD_BYTES:
        sys.exit(f"Payload is {size} bytes; keep it under {MAX_PAYLOAD_BYTES} (use an image URL instead of base64).")


def wait_for_publish(repo, tok, sent_at, timeout=300):
    """Poll the workflow run started by this dispatch until it finishes."""
    deadline = time.time() + timeout
    url = f"https://api.github.com/repos/{repo}/actions/runs?event=repository_dispatch&per_page=5"
    while time.time() < deadline:
        status, body = api("GET", url, tok)
        if status == 200:
            for run in body.get("workflow_runs", []):
                created = calendar.timegm(time.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
                if created >= sent_at - 5:
                    if run["status"] == "completed":
                        return run["conclusion"], run["html_url"]
                    break
        time.sleep(5)
    return None, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", help="payload as a JSON string")
    p.add_argument("--file", help="payload JSON file, or - for stdin")
    p.add_argument("--title")
    p.add_argument("--text")
    p.add_argument("--item", action="append", help='row "Label=Value" (repeatable)')
    p.add_argument("--chart", help="comma-separated numbers, oldest first")
    p.add_argument("--chart-title")
    p.add_argument("--image", help="picture URL (http/https) or data: URI")
    p.add_argument("--footer")
    p.add_argument("--repo", default=os.environ.get("EINK_REPO", DEFAULT_REPO))
    p.add_argument("--wait", action="store_true", help="wait until the image is published")
    p.add_argument("--dry-run", action="store_true", help="print the request body and exit")
    a = p.parse_args()

    try:
        payload = build_payload(a)
    except (ValueError, json.JSONDecodeError) as e:
        sys.exit(f"Bad input: {e}")
    check(payload)
    body = {"event_type": "eink", "client_payload": payload}
    if a.dry_run:
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return

    tok = token()
    sent_at = time.time()
    status, resp = api("POST", f"https://api.github.com/repos/{a.repo}/dispatches", tok, body)
    if status != 204:
        print(f"GitHub returned {status}: {resp}", file=sys.stderr)
        sys.exit(2)
    image = f"https://raw.githubusercontent.com/{a.repo}/display/dashboard.png"
    print(f"Sent. Image: {image}")
    if a.wait:
        conclusion, run_url = wait_for_publish(a.repo, tok, sent_at)
        if conclusion is None:
            print("Timed out waiting for the render workflow.", file=sys.stderr)
            sys.exit(3)
        print(f"Render {conclusion}: {run_url}")
        if conclusion != "success":
            sys.exit(2)
    print("The reader shows it at its next scheduled update (raw.githubusercontent.com may cache for up to 5 minutes).")


if __name__ == "__main__":
    main()
