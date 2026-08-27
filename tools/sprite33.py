#!/usr/bin/env python3
"""
sprite33.py -- extract Gauntlet's 16x16 sprites, derived from the Z80, not guessed.

GEOMETRY (read from $9DD2..$9E49, the sprite blitter):
    one record = 33 bytes = $21
        byte 0        attribute (one value for the whole 2x2 cell block)
        bytes 1..32   16 pixel rows, 2 bytes each, LEFT byte first
    16 px wide, 16 px tall, row-major, OPAQUE (LD (HL),E -- no mask, no OR).

INDEX (read from $A231..$A23F and $9F79..$9F82):
    $7B00 is a table of 255 little-endian pointers.  Sprite id N (1..255) uses
    the word at $7B00 + (N-1)*2.  Every pointer in the live image lands on a
    33-byte grid inside its bank.

SPRITE ID FOR AN ACTOR (read from $A47B..$A4B2):
    id = (IX+15) + dirslot + 8*phase
    dirslot = $7D0C[(IX+7) & 15]         direction bits: 1 up, 2 down, 4 left, 8 right
    phase   = 0 if (IX+14) bit6 clear, else 1 if bit7 clear, else 2

SCREEN ADDRESS (read from $B557):
    col = ((x - cam_x) & $7E) >> 1        screen_x = col * 8
    row = ((y - cam_y) & $7E) >> 1        screen_y = row * 8
    (There is no -8 fudge anywhere.  Confirmed against 10 live blits.)

Usage:
    python tools/sprite33.py build/live_now.bin --sheet build/sprites.png
    python tools/sprite33.py build/live_now.bin --id 212
"""
import sys

TABLE = 0x7B00          # id -> pointer, index (id-1)*2
DIRTAB = 0x7D0C         # (IX+7)&15 -> direction slot 0..7
REC = 33                # $21 bytes per sprite record
SLOTS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def sprite_addr(mem, sid):
    """Pointer for sprite id (1..255), exactly as $A231..$A23D computes it."""
    a = TABLE + (sid - 1) * 2
    return mem[a] | (mem[a + 1] << 8)


def decode(mem, base):
    """-> (attribute, rows) where rows[r][c] is 0/1 for a 16x16 sprite."""
    attr = mem[base]
    rows = []
    for r in range(16):
        lo = mem[base + 1 + r * 2]
        hi = mem[base + 2 + r * 2]
        rows.append([((lo if c < 8 else hi) >> (7 - (c % 8))) & 1 for c in range(16)])
    return attr, rows


def actor_sprite_id(mem, ix):
    """id = (IX+15) + $7D0C[(IX+7)&15] + 8*phase   -- $A47B..$A4B2."""
    slot = mem[DIRTAB + (mem[ix + 7] & 15)]
    f14 = mem[ix + 14]
    phase = 0 if not (f14 & 0x40) else (2 if (f14 & 0x80) else 1)
    return (mem[ix + 15] + slot + 8 * phase) & 0xFF


def screen_xy(x, y, cam_x, cam_y):
    """$B557.  Whole character cells only -- there is no sub-cell offset."""
    return (((x - cam_x) & 0x7E) >> 1) * 8, (((y - cam_y) & 0x7E) >> 1) * 8


def _sheet(mem, ids, path, zoom=4, cols=16):
    from PIL import Image, ImageDraw
    cw, ch = 16 * zoom + 6, 16 * zoom + 16
    n = len(ids)
    im = Image.new('RGB', (cols * cw, ((n + cols - 1) // cols) * ch), (24, 24, 32))
    dr = ImageDraw.Draw(im)
    for k, sid in enumerate(ids):
        base = sprite_addr(mem, sid)
        if base == 0:
            continue
        attr, rows = decode(mem, base)
        ink, paper, br = attr & 7, (attr >> 3) & 7, (attr >> 6) & 1
        v = 255 if br else 205

        def rgb(c):
            return (v if c & 2 else 0, v if c & 4 else 0, v if c & 1 else 0)
        cx, cy = (k % cols) * cw + 3, (k // cols) * ch + 14
        for r in range(16):
            for c in range(16):
                dr.rectangle([cx + c * zoom, cy + r * zoom,
                              cx + c * zoom + zoom - 1, cy + r * zoom + zoom - 1],
                             fill=rgb(ink if rows[r][c] else paper))
        dr.text((cx, cy - 12), '%d' % sid, fill=(200, 220, 255))
    im.save(path)
    print('wrote', path, im.size)


def main():
    mem = bytearray(open(sys.argv[1], 'rb').read())
    if '--id' in sys.argv:
        sid = int(sys.argv[sys.argv.index('--id') + 1], 0)
        base = sprite_addr(mem, sid)
        attr, rows = decode(mem, base)
        print('id %d -> $%04X  attr $%02X' % (sid, base, attr))
        for r in rows:
            print(''.join('#' if b else '.' for b in r))
    if '--sheet' in sys.argv:
        _sheet(mem, list(range(1, 256)), sys.argv[sys.argv.index('--sheet') + 1])


if __name__ == '__main__':
    main()
