#!/usr/bin/env python3
"""Render public webhook payloads. Usage: render.py payload.json OUT_DIR.

Heebo is bundled under fonts/ with its OFL license. Text is reordered once
with python-bidi, then drawn with Pillow BASIC (never a second bidi pass).
Supported emoji are drawn as monochrome vectors; other emoji become [U+...]
so an unsupported symbol cannot silently become a missing-glyph square.
"""
import json
import math
import os
import re
import sys
import time
import unicodedata
from functools import lru_cache
from pathlib import Path

from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import webhook_server as ws

SIZES = {"dashboard.png": (792, 528), "dashboard-portrait.png": (528, 792)}
EMOJI = {"📦": "box", "✉": "mail", "📬": "mail", "📮": "mail", "🕒": "clock",
         "⏰": "clock", "⌛": "clock", "⏳": "clock", "✅": "check", "✔": "check",
         "☑": "check", "★": "star", "⭐": "star", "⚠": "alert", "❤": "heart",
         "♥": "heart", "📈": "chart", "📊": "chart", "🏠": "home"}
TOKENS = {chr(0x2300 + i): name for i, name in enumerate(dict.fromkeys(EMOJI.values()))}
ICONS = {name: token for token, name in TOKENS.items()}


@lru_cache(maxsize=96)
def font(size, bold=False):
    path = Path(__file__).parent / "fonts" / "Heebo.ttf"
    if not path.exists():
        raise FileNotFoundError("Missing renderer/fonts/Heebo.ttf; upload the bundled font")
    f = ImageFont.truetype(str(path), max(10, int(size)), layout_engine=ImageFont.Layout.BASIC)
    f.set_variation_by_axes([700 if bold else 400])
    return f


def rtl(text):
    for c in str(text):
        direction = unicodedata.bidirectional(c)
        if direction in ("R", "AL"):
            return True
        if direction == "L":
            return False
    return False


def logical(text):
    out = []
    for c in str(text):
        if c in EMOJI:
            out.append(ICONS[EMOJI[c]])
        elif c in ("\ufe0e", "\ufe0f", "\u200d") or 0x1F3FB <= ord(c) <= 0x1F3FF:
            continue
        elif ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF:
            out.append(f"[U+{ord(c):X}]")
        else:
            out.append(c)
    return "".join(out)


def visual(text):
    text = logical(text)
    return get_display(text, base_dir="R" if rtl(text) else "L")


def icon(d, kind, x, y, size, fill=0):
    """Tiny original line illustrations; all coordinates scale with the icon."""
    def pts(values):
        return [(round(x + a * size), round(y + b * size)) for a, b in values]
    stroke = max(2, round(size * .055))
    def line(values):
        d.line(pts(values), fill=fill, width=stroke, joint="curve")
    def rect(a, b, c, e):
        d.rectangle((round(x+a*size), round(y+b*size), round(x+c*size), round(y+e*size)), outline=fill, width=stroke)
    if kind == "box":
        line([(.12,.30),(.5,.10),(.88,.30),(.88,.74),(.5,.94),(.12,.74),(.12,.30),(.5,.50),(.88,.30)])
        line([(.5,.50),(.5,.94)]); line([(.30,.20),(.68,.40),(.68,.58)])
    elif kind == "mail":
        rect(.08,.22,.92,.78); line([(.08,.22),(.5,.54),(.92,.22)])
    elif kind == "clock":
        d.ellipse((x+.1*size,y+.1*size,x+.9*size,y+.9*size), outline=fill,width=stroke)
        line([(.5,.25),(.5,.5),(.7,.6)])
    elif kind == "check":
        line([(.15,.52),(.39,.76),(.87,.23)])
    elif kind == "star":
        points = [(.5+(.44 if i%2==0 else .20)*math.sin(i*math.pi/5), .5-(.44 if i%2==0 else .20)*math.cos(i*math.pi/5)) for i in range(10)]
        d.polygon(pts(points), outline=fill, width=stroke)
    elif kind == "alert":
        line([(.5,.08),(.95,.86),(.05,.86),(.5,.08)]);line([(.5,.32),(.5,.57)])
        d.ellipse((x+.46*size,y+.68*size,x+.54*size,y+.76*size),fill=fill)
    elif kind == "heart":
        line([(.5,.85),(.12,.48),(.12,.28),(.25,.14),(.40,.14),(.5,.27),(.6,.14),(.75,.14),(.88,.28),(.88,.48),(.5,.85)])
    elif kind == "home":
        line([(.06,.45),(.5,.10),(.94,.45)]);rect(.2,.42,.8,.9);rect(.43,.6,.61,.9)
    else:
        line([(.08,.12),(.08,.86),(.94,.86)]);line([(.18,.67),(.4,.46),(.59,.56),(.88,.19)])


def width(text, f):
    return sum(f.size if c in TOKENS else f.getlength(c) for c in visual(text))


def fit(text, f, max_w):
    text = str(text)
    if width(text, f) <= max_w:
        return text
    while text and width(text + "...", f) > max_w:
        text = text[:-1]
    return text + "..." if width("...", f) <= max_w else ""


def draw_text(d, text, box, size=28, bold=False, align=None, fill=0):
    x0,y0,x1,y1 = box
    f = font(size,bold)
    text = fit(text,f,x1-x0)
    v = visual(text)
    tw = width(text,f)
    align = align or ("right" if rtl(text) else "left")
    x = x1-tw if align=="right" else x0+(x1-x0-tw)/2 if align=="center" else x0
    y = y0 + max(0,(y1-y0-f.size)/2)
    # Reserved neutral placeholders become icons and never reach a font.
    for run in re.split("([" + "".join(TOKENS) + "])",v):
        if not run:
            continue
        if run in TOKENS:
            icon(d,TOKENS[run],round(x),round(y),f.size,fill)
            x += f.size
        else:
            d.text((round(x),round(y)),run,font=f,fill=fill,anchor="lt")
            x += f.getlength(run)


def wrap(text, f, max_w):
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = (current+" "+word).strip()
            if current and width(candidate,f)>max_w:
                lines.append(current); current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def row_kind(label):
    label = str(label).lower()
    if any(s in label for s in ("שעות","hour","time","דדליין","deadline")):
        return "clock"
    if any(s in label for s in ("צ'יטה","חבילה","parcel","cheetah","pickup")):
        return "box"
    if any(s in label for s in ("בית","home")):
        return "home"
    return None


def draw_chart(d, pts, box, title=None):
    x0,y0,x1,y1 = box
    if title:
        draw_text(d,title,(x0,y0,x1,y0+32),22,True); y0 += 40
    lo,hi=min(pts),max(pts)
    plot_top,plot_bottom = y0+28,y1-30
    if plot_bottom <= plot_top:
        return
    draw_text(d,f"{hi:g}",(x0,y0,x1,y0+24),18)
    draw_text(d,f"{lo:g}",(x0,y1-24,x1,y1),18)
    d.line((x0,plot_bottom,x1,plot_bottom),fill=0,width=2)
    points=[(x0+i*(x1-x0)/(len(pts)-1),plot_bottom-(p-lo)/((hi-lo) or 1)*(plot_bottom-plot_top)) for i,p in enumerate(pts)]
    d.line(points,fill=0,width=3,joint="curve")
    ex,ey=points[-1]; d.ellipse((ex-4,ey-4,ex+4,ey+4),fill=0)
    draw_text(d,f"{pts[-1]:g}",(x0,max(y0,ey-30),x1,max(y0,ey-30)+24),18,True,"right")


def draw_monkey(d, w, h, caption):
    """Cute cartoon monkey face + banana + Hebrew caption, tuned for 1-bit."""
    s = min(w / 800, h / 480)
    def X(x): return round((x - 400) * s + w / 2)
    def Y(y): return round((y - 240) * s + h / 2 - 20 * s)
    def R(r): return max(1, round(r * s))
    K, W = 0, 255
    cx, cy, hr = 400, 210, 150
    # Ears: black with white inner circle.
    for ex in (cx - 150, cx + 150):
        ey = cy - 70
        d.ellipse((X(ex-72), Y(ey-72), X(ex+72), Y(ey+72)), fill=K)
        d.ellipse((X(ex-36), Y(ey-36), X(ex+36), Y(ey+36)), fill=W)
    # Head.
    d.ellipse((X(cx-hr), Y(cy-hr), X(cx+hr), Y(cy+hr)), fill=K)
    # Face: two overlapping white ellipses (eye area + muzzle).
    d.ellipse((X(cx-108), Y(cy-102), X(cx+108), Y(cy+32)), fill=W)
    d.ellipse((X(cx-92), Y(cy-24), X(cx+92), Y(cy+118)), fill=W)
    # Eyes with highlights.
    for ex in (cx - 52, cx + 52):
        ey = cy - 42
        d.ellipse((X(ex-20), Y(ey-20), X(ex+20), Y(ey+20)), fill=K)
        d.ellipse((X(ex-13), Y(ey-14), X(ex-1), Y(ey-2)), fill=W)
    # Nose.
    d.ellipse((X(cx-16), Y(cy+8), X(cx+16), Y(cy+30)), fill=K)
    # Smile.
    d.arc((X(cx-68), Y(cy+18), X(cx+68), Y(cy+104)), start=12, end=168, fill=K, width=R(7))
    # Hair tufts.
    for dx in (-30, 0, 30):
        d.arc((X(cx+dx-24), Y(cy-hr-30), X(cx+dx+24), Y(cy-hr+34)), start=200, end=340, fill=K, width=R(8))
    # Banana: tilted parametric crescent + stem.
    bcx, bcy, orad, icy, irad = 170, 330, 105, -38, 84
    tilt = math.radians(-22)
    def BP(px, py):
        qx, qy = px - bcx, py - bcy
        return (X(bcx + qx*math.cos(tilt) - qy*math.sin(tilt)),
                Y(bcy + qx*math.sin(tilt) + qy*math.cos(tilt)))
    outer = [BP(bcx + orad*math.cos(math.radians(t)), bcy + orad*math.sin(math.radians(t)))
             for t in range(14, 167, 4)]
    inner = [BP(bcx + irad*math.cos(math.radians(t)), bcy + icy + irad*math.sin(math.radians(t)))
             for t in range(162, 17, -4)]
    d.polygon(outer + inner, fill=K)
    stx, sty = bcx + orad*math.cos(math.radians(160)), bcy + orad*math.sin(math.radians(160))
    p0, p1 = BP(stx, sty), BP(stx-16, sty-28)
    d.line((p0[0], p0[1], p1[0], p1[1]), fill=K, width=R(9))
    d.ellipse((p1[0]-R(8), p1[1]-R(8), p1[0]+R(8), p1[1]+R(8)), fill=K)
    # Caption (bidi-reordered once, like draw_text).
    f = font(round(72 * s), True)
    v = visual(caption)
    tw = f.getlength(v)
    d.text((round(w/2 - tw/2), Y(430)), v, font=f, fill=K, anchor="lt")


def shipments_data(payload):
    """Use a fixed data file, or an explicit inline shipments object."""
    if "shipments" in payload:
        data = payload["shipments"]
    else:
        data = json.loads((Path(__file__).resolve().parent.parent / "data" / "shipments.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("shipments must contain an items list")
    if not 1 <= len(data["items"]) <= 8:
        raise ValueError("shipments screen supports 1-8 items; split longer lists")
    for item in data["items"]:
        if not isinstance(item, dict) or not item.get("name") or not item.get("status"):
            raise ValueError("each shipment needs name and status")
    return data


def draw_shipments(data, w, h):
    """Numbered, two-line RTL rows. Reject overflow instead of hiding items."""
    if min(w, h) < 320:
        raise ValueError("shipments screen needs at least 320 pixels per side")
    canvas = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(canvas)
    m, top, bottom = 24, 90, h - 42
    icon(d, "box", w-m-42, 22, 38)
    draw_text(d, data.get("title", "חבילות ומשלוחים"), (m, 18, w-m-58, 65), 34, True, "right")
    d.line((m, 78, w-m, 78), fill=0, width=2)
    items = data["items"]
    row_h = (bottom-top)/len(items)
    available = w-2*m-42
    layouts = None
    for name_size, status_size in ((21,18), (20,17), (18,16), (16,14)):
        candidate = []
        valid = True
        for item in items:
            nl = wrap(item["name"], font(name_size, True), available)
            sl = wrap(item["status"], font(status_size), available)
            height = len(nl)*(name_size+4)+len(sl)*(status_size+4)+8
            if height > row_h or any(width(line,font(name_size,True)) > available for line in nl) or any(width(line,font(status_size)) > available for line in sl):
                valid = False
                break
            candidate.append((nl,sl,height))
        if valid:
            layouts = candidate
            break
    if layouts is None:
        raise ValueError("shipments text does not fit: shorten name/status, never silently truncate")
    for i,(nl,sl,height) in enumerate(layouts):
        y = top + i*row_h
        draw_text(d, str(i+1), (w-m-30,y,w-m,y+row_h), 21,True,"center")
        ty = y + (row_h-height)/2 + 4
        for line in nl:
            draw_text(d,line,(m,ty,w-m-42,ty+name_size+4),name_size,True,"right")
            ty += name_size+4
        for line in sl:
            draw_text(d,line,(m,ty,w-m-42,ty+status_size+4),status_size,False,"right")
            ty += status_size+4
        if i+1<len(items):
            d.line((m,round(y+row_h),w-m,round(y+row_h)),fill=0,width=1)
    footer = data.get("footer") or "עודכן " + str(data.get("updated_at", ""))
    if width(footer,font(17,True)) > w-2*m:
        raise ValueError("shipments footer does not fit")
    d.rectangle((0,h-34,w,h),fill=0)
    draw_text(d,footer,(m,h-31,w-m,h-3),17,True,"right",255)
    return canvas.point(lambda px:255 if px>=128 else 0).convert("1")


def render_payload(payload, w, h, received=None, count=0, image_loader=ws.load_image):
    """All legacy fields remain supported, including unknown key/value rows."""
    p=dict(payload)
    if str(p.get("mode", "")).strip().lower() == "shipments":
        return draw_shipments(shipments_data(p), w, h)
    if str(p.get("mode","")).strip().lower()=="monkey":
        canvas=Image.new("L",(w,h),255)
        draw_monkey(ImageDraw.Draw(canvas),w,h,str(p.get("caption") or "קוף!"))
        return canvas.point(lambda px:255 if px>=128 else 0).convert("1")
    title=p.pop("title",None) or "Webhook"
    footer=p.pop("footer",None)
    text=p.pop("text",None)
    rows=ws.normalize_items(p.pop("items",None))
    src=p.pop("image",None)
    image=image_loader(str(src),count) if src else None
    pts=ws.chart_values(p.pop("chart",None))
    if pts and not all(math.isfinite(v) for v in pts):
        pts=None
    chart_title=p.pop("chart_title",None)
    rows += [(str(k),json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)) for k,v in p.items()]
    # Draw at canonical resolution, then adapt even small server query sizes safely.
    target=(w,h)
    if min(w,h)<320:
        scale=528/min(w,h);w,h=round(w*scale),round(h*scale)
    canvas=Image.new("L",(w,h),255);d=ImageDraw.Draw(canvas)
    m=32; footer_top=h-40; y=126
    is_rtl=rtl(title) or (not any(unicodedata.bidirectional(c)=="L" for c in str(title)) and any(rtl(a) for a,b in rows))
    heading_kind="box" if any(s in str(title).lower() for s in ("דואר","איסוף","pickup","parcel")) else "chart" if pts else "mail"
    ix=w-m-62 if is_rtl else m
    d.rounded_rectangle((ix,28,ix+62,90),radius=12,fill=0)
    icon(d,heading_kind,ix+10,38,42,255)
    titlebox=(m,28,w-m-82,94) if is_rtl else (m+82,28,w-m,94)
    draw_text(d,title,titlebox,44,True,"right" if is_rtl else "left")
    d.line((m,110,w-m,110),fill=0,width=3)
    tx0,tx1,bottom=m,w-m,footer_top-16
    has_text=bool(text) or bool(rows)
    if image is not None or pts:
        if not has_text:
            gb=(m,y,w-m,bottom)
        elif w>=h:
            gx=int(w*.53)
            gb=(m,y,w-gx-20,bottom) if is_rtl else (gx,y,w-m,bottom)
            if is_rtl:tx0=w-gx+6
            else:tx1=gx-24
        else:
            gy=y+int((bottom-y)*.48);gb=(m,gy,w-m,bottom);bottom=gy-16
        if image is not None and pts:
            mid=(gb[1]+gb[3])//2
            ws.draw_image(canvas,image,(gb[0],gb[1],gb[2],mid-8))
            draw_chart(d,pts,(gb[0],mid+8,gb[2],gb[3]),chart_title)
        elif image is not None:ws.draw_image(canvas,image,gb)
        else:draw_chart(d,pts,gb,chart_title)
    if text:
        lines=wrap(text,font(28),tx1-tx0)
        for line in lines[:ws.MAX_TEXT_LINES]:
            if y+38>bottom:break
            draw_text(d,line,(tx0,y,tx1,y+38),28);y+=38
        y+=8
    free=bottom-y
    row_h=min(102,max(72,free/max(1,min(len(rows),6))))
    shown=0
    for label,value in rows:
        if y+row_h>bottom+1:break
        kind=None if any(c in EMOJI for c in str(label)) else row_kind(label)
        rx0,rx1=tx0,tx1
        if kind and row_h>=64:
            ix=tx1-40 if is_rtl else tx0
            icon(d,kind,ix,y+(row_h-30)/2,30)
            if is_rtl:rx1-=52
            else:rx0+=52
        span=rx1-rx0
        if span<440 or width(label,font(25))>span*.5-18 or width(value,font(32,True))>span*.5-18:
            # Narrow graphics/portrait layouts stack label and value rather than collide.
            draw_text(d,label,(rx0,y+6,rx1,y+row_h*.43),20,False,"right" if is_rtl else "left")
            draw_text(d,value,(rx0,y+row_h*.43,rx1,y+row_h-9),28,True,"right" if is_rtl else "left")
        else:
            mid=(rx0+rx1)/2
            lb=(mid+12,y,rx1,y+row_h) if is_rtl else (rx0,y,mid-12,y+row_h)
            vb=(rx0,y,mid-12,y+row_h) if is_rtl else (mid+12,y,rx1,y+row_h)
            draw_text(d,label,lb,25,False,"right" if is_rtl else "left")
            draw_text(d,value,vb,32,True,"left" if is_rtl else "right")
        y+=row_h;shown+=1
        if shown<len(rows):d.line((tx0,y,tx1,y),fill=0,width=1)
    if footer is None:
        footer=(f"Webhook #{count}, received {time.strftime('%H:%M:%S',time.localtime(received))}" if received else "No webhook received yet")
    if shown<len(rows):footer=str(footer)+f" | +{len(rows)-shown} rows"
    d.rectangle((0,footer_top,w,h),fill=0)
    draw_text(d,footer,(m,footer_top+5,w-m,h-5),20,True,"right" if is_rtl else "left",255)
    # Threshold typography instead of dithering it; photos were dithered separately.
    canvas=canvas.point(lambda p:255 if p>=128 else 0).convert("1")
    if canvas.size!=target:canvas=canvas.resize(target,Image.Resampling.NEAREST)
    return canvas


def main():
    if len(sys.argv)!=3:sys.exit(__doc__)
    src,out=sys.argv[1:]
    with open(src,"rb") as f:payload=ws.parse_body(f.read())
    os.makedirs(out,exist_ok=True)
    received=time.time();count=int(os.environ.get("RUN_NUMBER","0") or 0)
    for name,(w,h) in SIZES.items():render_payload(payload,w,h,received,count).save(os.path.join(out,name))
    with open(os.path.join(out,"payload.json"),"w",encoding="utf-8") as f:json.dump(payload,f,indent=2,ensure_ascii=False)
    with open(os.path.join(out,"status.json"),"w",encoding="utf-8") as f:json.dump({"render":count,"received":time.strftime("%Y-%m-%dT%H:%M:%S%z",time.localtime(received))},f)
    print(f"rendered #{count}: {', '.join(SIZES)}")


if __name__=="__main__":main()

