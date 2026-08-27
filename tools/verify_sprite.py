#!/usr/bin/env python3
"""Independent sprite decoder -- written from scratch, no reuse of the
colleague's spritebank.py.  Decodes N-byte records as
   +0 attribute, then `rows` rows of `wbytes` bitmap bytes.
Renders a labelled contact sheet so it can be LOOKED at.
"""
import sys, os
from PIL import Image, ImageDraw

PAL = [(0,0,0),(0,0,205),(205,0,0),(205,0,205),(0,205,0),(0,205,205),
       (205,205,0),(205,205,205)]
BPAL= [(0,0,0),(0,0,255),(255,0,0),(255,0,255),(0,255,0),(0,255,255),
       (255,255,0),(255,255,255)]

def decode(rec, rows=16, wbytes=2, attr_first=True):
    """rec: bytes.  returns (attr, [[bit,...] per row])"""
    if attr_first:
        attr = rec[0]; body = rec[1:]
    else:
        attr = 0x47; body = rec
    px = []
    for r in range(rows):
        line = []
        for c in range(wbytes):
            i = r*wbytes + c
            b = body[i] if i < len(body) else 0
            for k in range(7,-1,-1):
                line.append((b>>k)&1)
        px.append(line)
    return attr, px

def img(attr, px, scale=3):
    h = len(px); w = len(px[0])
    ink = attr & 7; paper = (attr>>3)&7; bright = attr & 0x40
    P = BPAL if bright else PAL
    im = Image.new('RGB',(w,h))
    for y in range(h):
        for x in range(w):
            im.putpixel((x,y), P[ink] if px[y][x] else P[paper])
    return im.resize((w*scale,h*scale), Image.NEAREST)

def sheet(data, base, n, stride=33, rows=16, wbytes=2, cols=8, scale=3,
          attr_first=True, label=True, out='out.png'):
    cw, ch = wbytes*8*scale, rows*scale
    pad = 14 if label else 2
    W = cols*(cw+6)+6; H = ((n+cols-1)//cols)*(ch+pad+6)+6
    sh = Image.new('RGB',(W,H),(30,30,40))
    d = ImageDraw.Draw(sh)
    for k in range(n):
        a = base + k*stride
        rec = data[a:a+stride]
        if len(rec) < stride: break
        attr, px = decode(rec, rows, wbytes, attr_first)
        x = (k%cols)*(cw+6)+6; y=(k//cols)*(ch+pad+6)+6
        sh.paste(img(attr,px,scale),(x,y))
        if label:
            d.text((x, y+ch+1), f'{k} ${a:04X} a{attr:02X}', fill=(200,200,200))
    sh.save(out)
    return out
