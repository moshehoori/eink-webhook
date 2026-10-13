---
name: eink-display
description: Show text, status rows, a line chart or a picture on the user's Xteink X3 e-ink display (CrossPoint Stock Ticker custom image page) by sending a webhook that GitHub Actions renders into an image. Use when asked to put, show, post, display or update something on the e-ink screen, e-reader, X3, dashboard or "the display"; for build/CI status, reminders, home sensor readings, short notes or summaries meant to sit on a glanceable screen.
---

# E-ink display

Push content to the user's e-ink dashboard. A webhook goes to GitHub, an Actions workflow
renders it into a black-and-white image, and the reader downloads that image on its next
scheduled wake.

## Send

```bash
python3 scripts/send.py --title "Build #482 passed" --item "Branch=main" --item "Duration=3m 12s" --wait
python3 scripts/send.py --title "Living room" --item "Temperature=23.4 °C" --item "Humidity=41 %" \
    --chart 21.8,22.1,22.6,23.0,23.4 --chart-title "Temperature, last 5 h" --wait
python3 scripts/send.py --json '{"title": "Front door", "text": "Motion detected", "image": "https://example.com/snap.jpg"}'
```

`--wait` blocks until the image is published (about 30-60 s) and prints the run URL.
`--dry-run` prints the request without sending. Uses `EINK_GITHUB_TOKEN`, `GITHUB_TOKEN`
or the logged-in `gh` CLI; `EINK_REPO` overrides the repo (default `moshehoori/eink-webhook`).

## Payload

All fields optional. Each webhook **replaces** the whole screen (nothing is merged).

| Field | Shown as |
| --- | --- |
| `title` | One-line heading, about 25 characters before it is cut with "..." |
| `text` | Paragraph, wrapped, max 6 lines (~43 chars/line, ~20 when a graphic is beside it) |
| `items` | Rows: label left, value right in bold. `[["Label", "value"], ...]` or `{"label","value"}` objects |
| `chart` | 2+ numbers, oldest first, drawn as a line with min/max/latest labels |
| `chart_title` | Heading above the chart |
| `image` | http(s) URL or `data:image/png;base64,...`; dithered to black and white |
| `footer` | Replaces the default "Webhook #N, received HH:MM:SS" bar; `" "` for an empty bar |
| other keys | Extra rows `key: value` |

Layout: landscape 792x528 puts text left and graphics right; with no text, graphics take the full
width; image and chart together stack. About 8 rows fit with no text.

## Rules

1. Put the key fact in `title`; keep it under ~25 characters. At most ~6 rows.
2. Format values yourself: units, rounding, thousands separators ("23.4 °C", "1,204").
3. Black and white only: no colour, emphasis comes from words and order.
4. Left-to-right text only: Hebrew/Arabic render reversed, emoji may show as boxes.
5. At most 10 top-level fields (GitHub limit). Prefer image URLs over base64 (size limit).
6. To show several events, send them together as `items`; a new webhook replaces the old one.
7. **The image is public** (public repo, raw URL). Never send passwords, tokens, addresses,
   health or other private data.

## Timing

Publishing takes ~1 minute; raw.githubusercontent.com may serve the old image for up to 5
minutes; the reader then shows it at its next refresh (its interval, often 15-30 min, or
hourly overnight). Tell the user it is not instant. Current image:
`https://raw.githubusercontent.com/moshehoori/eink-webhook/display/dashboard.png`
(`dashboard-portrait.png` for portrait). `payload.json` on the `display` branch shows what
was last sent.

## Troubleshooting

- `GitHub returned 404`: wrong repo, or the token lacks access to it.
- `GitHub returned 403/401`: token missing "Contents: read and write" on the repo.
- Render failed: open the run URL; usually invalid JSON in `--json`.
- Screen not updated: check the reader's Stock Ticker settings, Custom image mode
  (Only the image / Extra page) and URL, and that ticker mode is running.
