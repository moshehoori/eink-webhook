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


def draw_infographic(data, w, h):
    """Render the approved four-tile Hebrew e-ink dashboard, landscape or portrait."""
    if not isinstance(data, dict):
        raise ValueError("infographic payload must be an object")
    trip = data.get("trip", {})
    parcels = data.get("parcels", {})
    weather = data.get("weather", {})
    wolt = data.get("wolt", {})
    required = [(trip,"label","value","detail"),(parcels,"label","value","detail"),
                (weather,"label","value","detail","rain"),(wolt,"label","value")]
    if any(not all(isinstance(o.get(k),str) and o[k] for k in keys) for o,*keys in required):
        raise ValueError("infographic needs trip/parcels/weather/wolt labels and values")
    if min(w,h) < 320:
        raise ValueError("infographic needs at least 320 pixels per side")
    canvas=Image.new("L",(w,h),255); d=ImageDraw.Draw(canvas)
    portrait=h>w
    margin=18 if portrait else 22
    gap=12 if portrait else 14
    header_h=54 if portrait else 64
    bottom=h-(16 if portrait else 18)
    cw=(w-2*margin-gap)//2
    ch=(bottom-header_h-gap)//2
    rects={"parcels":(margin,header_h,margin+cw,header_h+ch),
           "trip":(margin+cw+gap,header_h,w-margin,header_h+ch),
           "wolt":(margin,header_h+ch+gap,margin+cw,bottom),
           "weather":(margin+cw+gap,header_h+ch+gap,w-margin,bottom)}
    for box in rects.values():
        d.rounded_rectangle(box,radius=16,fill=255,outline=0,width=3)
    def title(obj): return str(obj.get("label",""))
    def value(obj): return str(obj.get("value",""))
    def detail(obj,key="detail"): return str(obj.get(key,""))
    def center(obj,s): return obj[0]+(obj[2]-obj[0])/2
    def text(s,x,y,size,bold=True,align="right"):
        draw_text(d,str(s),(int(x-180),int(y),int(x+180),int(y+size+8)),size,bold,align)
    def plane(cx,cy,size):
        sc=5; q=Image.new("L",(100*sc,136*sc),255); qd=ImageDraw.Draw(q)
        def poly(pts): qd.polygon([(int(a*sc),int(b*sc)) for a,b in pts],fill=0)
        poly([(50,5),(56,18),(57,91),(54,115),(50,130),(46,115),(43,91),(44,18)])
        poly([(44,45),(9,60),(4,67),(7,72),(43,68)])
        poly([(56,45),(91,60),(96,67),(93,72),(57,68)])
        poly([(44,96),(23,108),(20,112),(23,115),(47,108)])
        poly([(56,96),(77,108),(80,112),(77,115),(53,108)])
        q=q.resize((size,int(size*1.36)),Image.Resampling.LANCZOS).point(lambda p:255 if p>=128 else 0).convert("1")
        canvas.paste(q,(round(cx-size/2),round(cy-size*1.36/2)))
    def parcels_icon(cx,cy,size):
        s=size/105
        for ox,oy,k in [(-20,12,.72),(18,-13,.8)]:
            r=29*k*s; x=cx+ox*s; y=cy+oy*s
            d.polygon([(x,y-r*.62),(x+r*.72,y-r*.25),(x+r*.72,y+r*.48),(x,y+r*.85),(x-r*.72,y+r*.48),(x-r*.72,y-r*.25)],outline=0,width=max(2,int(4*s)))
            d.line([(x-r*.72,y-r*.25),(x,y+r*.13),(x+r*.72,y-r*.25)],fill=0,width=max(2,int(4*s)))
            d.line((x,y+r*.13,x,y+r*.85),fill=0,width=max(2,int(4*s)))
    def cloud(cx,cy,size):
        s=size/100
        d.ellipse((cx-40*s,cy-8*s,cx-4*s,cy+28*s),fill=0)
        d.ellipse((cx-21*s,cy-35*s,cx+22*s,cy+28*s),fill=0)
        d.ellipse((cx+9*s,cy-13*s,cx+47*s,cy+28*s),fill=0)
        d.rectangle((cx-25*s,cy+5*s,cx+30*s,cy+28*s),fill=0)
        for dx in (-23,0,23): d.line((cx+dx*s,cy+36*s,cx+(dx-7)*s,cy+52*s),fill=0,width=max(3,int(6*s)))
    def weather_icon(cx,cy,size):
        """Simple partly-cloudy mark: sun behind a small cloud, no rain strokes."""
        s=size/100
        sw=max(2,int(3*s))
        # Sun and rays sit above/right of the cloud.
        sx=cx+13*s; sy=cy-11*s; r=17*s
        d.ellipse((sx-r,sy-r,sx+r,sy+r),outline=0,width=sw)
        for dx,dy in [(0,-1),(1,0),(0,1),(-1,0),(0.71,-0.71),(0.71,0.71),(-0.71,0.71),(-0.71,-0.71)]:
            d.line((sx+dx*22*s,sy+dy*22*s,sx+dx*29*s,sy+dy*29*s),fill=0,width=sw)
        # Cloud base, deliberately within the left icon column.
        bx=cx-12*s; by=cy+13*s
        d.ellipse((bx-30*s,by-5*s,bx-7*s,by+18*s),fill=255,outline=0,width=sw)
        d.ellipse((bx-19*s,by-23*s,bx+9*s,by+18*s),fill=255,outline=0,width=sw)
        d.ellipse((bx+1*s,by-8*s,bx+25*s,by+18*s),fill=255,outline=0,width=sw)
        d.line((bx-19*s,by+15*s,bx+13*s,by+15*s),fill=0,width=sw)
        d.line((bx-24*s,by+15*s,bx+19*s,by+15*s),fill=0,width=sw)
    def wallet_icon(cx,cy,size):
        s=size/100; sw=max(3,int(6*s))
        d.rounded_rectangle((cx-48*s,cy-31*s,cx+33*s,cy+32*s),radius=max(5,int(11*s)),outline=0,width=sw)
        d.line((cx-38*s,cy-20*s,cx+26*s,cy-20*s),fill=0,width=max(3,int(5*s)))
        d.rounded_rectangle((cx+5*s,cy-10*s,cx+52*s,cy+19*s),radius=max(4,int(7*s)),fill=255,outline=0,width=max(3,int(5*s)))
        d.ellipse((cx+18*s,cy-1*s,cx+26*s,cy+7*s),fill=0)
    # Headline and simple four-tile marker.
    head_size=30 if portrait else 35
    draw_text(d,data.get("title","היום שלנו"),(margin,6,w-margin-46,header_h-3),head_size,True,"right")
    sx=w-margin-22; sy=21 if not portrait else 17
    for dx,dy in [(0,0),(14,0),(0,14),(14,14)]:
        d.rectangle((sx+dx-4,sy+dy-4,sx+dx+3,sy+dy+3),outline=0,width=2)
    d.line((margin,header_h-10,w-margin,header_h-10),fill=0,width=2)
    # Per-tile type scales to respect the narrow portrait layout.
    if not portrait:
        icon_size=88; value_size=82; label_size=28; detail_size=28
        # Travel, upper right
        b=rects["trip"]; plane(b[0]+66,b[1]+110,66)
        draw_text(d,title(trip),(b[0]+92,b[1]+10,b[2]-16,b[1]+51),30,True,"right")
        draw_text(d,value(trip),(b[0]+100,b[1]+42,b[2]-40,b[1]+131),84,True,"center")
        draw_text(d,detail(trip),(b[0]+20,b[3]-48,b[2]-20,b[3]-10),28,True,"center")
        # Parcels, upper left
        b=rects["parcels"]; parcels_icon(b[0]+68,b[1]+107,112)
        draw_text(d,title(parcels),(b[0]+88,b[1]+10,b[2]-15,b[1]+51),29,True,"right")
        draw_text(d,value(parcels),(b[0]+100,b[1]+42,b[2]-40,b[1]+131),84,True,"center")
        draw_text(d,detail(parcels),(b[0]+20,b[3]-48,b[2]-20,b[3]-10),28,True,"center")
        # Weather, lower right
        b=rects["weather"]; weather_icon(b[0]+52,b[1]+100,76)
        draw_text(d,title(weather),(b[0]+13,b[1]+8,b[2]-15,b[1]+47),27,True,"right")
        draw_text(d,value(weather),(b[0]+95,b[1]+43,b[2]-18,b[1]+102),54,True,"center")
        draw_text(d,detail(weather),(b[0]+105,b[1]+106,b[2]-18,b[1]+143),26,True,"center")
        draw_text(d,detail(weather,"rain"),(b[0]+8,b[1]+174,b[2]-12,b[1]+205),20,True,"center")
        # Wallet, lower left
        b=rects["wolt"]; wallet_icon(b[0]+68,b[1]+106,108)
        draw_text(d,title(wolt),(b[0]+95,b[1]+10,b[2]-15,b[1]+51),30,True,"right")
        draw_text(d,value(wolt),(b[0]+95,b[1]+64,b[2]-15,b[1]+128),56,True,"center")
    else:
        # Portrait: stack the title, value, large icon, and supporting detail.
        for name,obj in [("trip",trip),("parcels",parcels),("weather",weather),("wolt",wolt)]:
            b=rects[name]; cx=(b[0]+b[2])/2
            draw_text(d,title(obj),(b[0]+10,b[1]+14,b[2]-10,b[1]+53),24,True,"center")
            if name=="trip":
                draw_text(d,value(obj),(b[0]+10,b[1]+61,b[2]-10,b[1]+133),72,True,"center")
                plane(cx,b[1]+218,80)
                draw_text(d,detail(obj),(b[0]+8,b[3]-47,b[2]-8,b[3]-10),23,True,"center")
            elif name=="parcels":
                draw_text(d,value(obj),(b[0]+10,b[1]+61,b[2]-10,b[1]+133),72,True,"center")
                parcels_icon(cx,b[1]+213,130)
                draw_text(d,detail(obj),(b[0]+8,b[3]-47,b[2]-8,b[3]-10),23,True,"center")
            elif name=="weather":
                draw_text(d,value(obj),(b[0]+8,b[1]+56,b[2]-8,b[1]+116),44,True,"center")
                weather_icon(cx,b[1]+182,86)
                draw_text(d,detail(obj),(b[0]+8,b[1]+246,b[2]-8,b[1]+280),22,True,"center")
                draw_text(d,detail(obj,"rain"),(b[0]+8,b[3]-50,b[2]-8,b[3]-12),17,True,"center")
            else:
                draw_text(d,value(obj),(b[0]+8,b[1]+75,b[2]-8,b[1]+151),52,True,"center")
                wallet_icon(cx,b[1]+220,110)
    return canvas.point(lambda p:255 if p>=128 else 0).convert("1")



def render_payload(payload, w, h, received=None, count=0, image_loader=ws.load_image):
    """All legacy fields remain supported, including unknown key/value rows."""
    p=dict(payload)
    if str(p.get("mode", "")).strip().lower() == "infographic":
        return draw_infographic(p, w, h)
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

