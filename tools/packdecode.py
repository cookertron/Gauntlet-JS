#!/usr/bin/env python3
"""
packdecode.py -- decode a Gauntlet dungeon pack straight off the tape bytes
into the 32x32 map the original builds at $8000, plus the actor list and the
player start.

Every rule here is transcribed from the original's own code; the addresses are
named so each one can be re-read.  Nothing is inferred from how a level
"ought" to look.

THE CONTAINER  (a side-2 tape block, loaded to $C000 by $9203)
--------------------------------------------------------------
    +$000 .. +$005   6 bytes the loader OVERWRITES with zero, so that the
                     PUSH HL / RET at $927B lands on six NOPs
    +$006 .. +$0B8   the pack's own stub, executed at $C006
    +$0B9 .. +$0CC   ten 16-bit little-endian SUB-BLOCK LENGTHS (0 = unused)
    +$0CD .. +$0CF   three scratch bytes the stub patches at run time
    +$0D0 ..         the sub-blocks, back to back
    last byte        $FF -- $9252 keeps re-loading until it sees it

The stub ($C006) branches on the expected tape flag:

  flag $80 (the FIRST pack)   copy 1114 bytes from +$0D0 to $6F80 and the next
                              552 to $DDD8, verbatim.  $84CC := $80.
  flag $C0 (the other 30)     pick THREE sub-block indices at random (0..7 from
                              $C090, or 8/9 from $C09F on a bonus level),
                              concatenate them at $4000, then copy the same
                              1114+552 bytes out of $4000.  $84CC := 7.

$91D7 then LDIRs $6F80 (1114 bytes) and $DDD8 (552 bytes) back into $C000, so
the RECORD LIST is those 1666 bytes; $91F0 strides it by the length byte at +0
with a 9th length bit in bit 7 of +2, and stops on the record the level number
selects.  A sub-block IS one record.

A RECORD
--------
    +0  record length, low byte      (9th bit = bit 7 of +2)
    +1  flags   bit0 -> $847E bit5      bit1 -> $847E bit4
                bit2 -> the "one random $36 survives" pass at $9B5F
                bits3-5 -> tile-bank index ($9AB9)
                bit6 -> suppress the $5A3E attribute clear
                bit7 -> suppress the $423E bitmap clear AND drop the LEFT
                        border column (the fixed table is walked for 3 bytes
                        instead of 4)
    +2  bit7 = the 9th length bit;  bits3-5 = colour-scheme index ($9AB9)
    +3  LENGTH IN BYTES of the vector table that follows
    +4 .. +3+(+3)   the VECTOR TABLE  (walls and doors, turtle graphics)
    the rest        the RLE CELL STREAM

THE VECTOR TABLE ($98C6)
------------------------
Two entry kinds, told apart by the top 3 bits ("tag") of consecutive bytes:

  POINT (2 bytes)  b0 = tag<<5 | column,  b1 = tag<<5 | row   (same tag)
                   sets the pen to tag<<5, moves the cursor to (row,col)
                   ABSOLUTELY, and paints there.
  RUN   (1 byte)   b0 = dir<<5 | (count-1)
                   steps the cursor `count` times by delta[dir] from the
                   8-entry table at $7DA4 and paints each cell.
                   The PEN persists from the last point entry; only a door
                   pen is re-oriented by the run direction ($991F..$992D).

The walker takes a pair as a POINT when tag(b0)==tag(b1) unless the lookahead
at $98D4..$98EC says otherwise -- transcribed literally below.

The pen selects what gets painted ($9945):
   >= $C0  wall  ($99BA, connectivity bits)      $80  door $11
   == $40  door $12                              else tile $36

THE RLE CELL STREAM ($9871)
---------------------------
Cursor starts at $8000 and only ever moves forward by one cell.
   $00..$12   repeat the CURRENT tile (n+1) more times
   $13..$7F   n is the new current tile; paint it once
   $80..$FF   skip (n & $7F) + 1 cells, leaving what the vectors put there

A painted tile ($9960 / $9A7A):
   < $3F   stored in the map cell as-is
   = $3F   the PLAYER START; the cell is left 0
   > $3F   an ACTOR: append (x, y, type, rand&$C0) to the list at $5C00;
           the cell is left 0.   type = (((t>>1)&$1C) | (t&3)) * 8

Usage:
    python tools/packdecode.py 1 0            pack 1, record 0
    python tools/packdecode.py 1 0 --grid     print the grid
    python tools/packdecode.py --lengths      the sub-block table of every pack
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLOCKS2 = os.path.join(ROOT, 'build', 'blocks2')

# $7DA4: eight signed 16-bit cursor deltas, indexed by the tag.
DELTA = [-32, -31, 1, 33, 32, 31, -1, -33]
# $7DB4: the fixed border table; four bytes, or three when flags bit 7 is set.
BORDER = bytes((0xFF, 0xE0, 0xDE, 0x9E))

HDR = 0xD0                     # first sub-block
LENTAB = 0xB9                  # ten 16-bit sub-block lengths
FIRST_COPY = 0x45A             # $6F80 gets this many bytes
SECOND_COPY = 0x228            # $DDD8 gets this many


def pack_path(n):
    return os.path.join(BLOCKS2, 'Gauntlet_-_Side_2.b%02d.body.bin' % n)


def load_pack(n):
    return open(pack_path(n), 'rb').read()


def sub_lengths(pack):
    out = []
    for i in range(10):
        v = pack[LENTAB + 2 * i] | (pack[LENTAB + 2 * i + 1] << 8)
        out.append(v)
    return out


def record_list(pack, subs=None):
    """The 1666 bytes $91D7 assembles at $C000, as a bytearray.

    `subs` is None for the $80 pack (the sub-blocks are taken in order, exactly
    as they lie on the tape) or a 3-tuple of indices for a $C0 pack, which is
    what the stub's three random draws produce.
    """
    if subs is None:
        body = pack[HDR:]
    else:
        lens = sub_lengths(pack)
        starts = [HDR]
        for n in lens:
            starts.append(starts[-1] + n)
        body = b''.join(pack[starts[i]:starts[i] + lens[i]] for i in subs)
    buf = bytearray(FIRST_COPY + SECOND_COPY)
    n = min(len(body), len(buf))
    buf[:n] = body[:n]
    return buf


def record_offsets(buf, count=8):
    """$91F0's walk: stride by the length byte at +0, 9th bit in bit 7 of +2."""
    offs = []
    p = 0
    for _ in range(count):
        if p + 4 > len(buf) or buf[p] == 0 and buf[p + 2] == 0:
            break
        offs.append(p)
        p += buf[p] + (0x100 if buf[p + 2] & 0x80 else 0)
    return offs


# --------------------------------------------------------------------------
# the map itself.  Cells are addressed exactly as the original addresses them,
# by a 16-bit pointer into $8000..$83FF, so that the wrap arithmetic is the
# original's and not a modulo.
# --------------------------------------------------------------------------
class Map:
    def __init__(self):
        self.cell = bytearray(0x0400)      # $8000..$83FF
        self.spill = {}                    # anything the RLE writes past $83FF
        self.actors = []
        self.thirtysix = []
        self.player = None
        self.cur = 0x00                    # C' -- the current tile
        self.pen = 0x00                    # B' -- the current vector pen

    # -- $8000-relative access ---------------------------------------------
    def get(self, hl):
        if 0x8000 <= hl < 0x8400:
            return self.cell[hl - 0x8000]
        return self.spill.get(hl, 0)

    def put(self, hl, v):
        if 0x8000 <= hl < 0x8400:
            self.cell[hl - 0x8000] = v & 0xFF
        else:
            self.spill[hl] = v & 0xFF

    # -- $9A18 / $9A2D / $9A3D / $9A51 -------------------------------------
    @staticmethod
    def up(hl):
        h, l = hl >> 8, hl & 0xFF
        a = l - 0x20
        l = a & 0xFF
        if a < 0:
            h = (h - 1) & 0xFF
            if h == 0x7F:
                h = (h + 4) & 0xFF
        return (h << 8) | l

    @staticmethod
    def right(hl):
        h, l = hl >> 8, hl & 0xFF
        l = (l + 1) & 0xFF
        if (l & 0x1F) == 0:
            l = (l - 0x20) & 0xFF
        return (h << 8) | l

    @staticmethod
    def down(hl):
        h, l = hl >> 8, hl & 0xFF
        a = l + 0x20
        l = a & 0xFF
        if a > 0xFF:
            h = (h + 1) & 0xFF
            if ((h - 4) & 0xFF) == 0x80:
                h = 0x80
        return (h << 8) | l

    @staticmethod
    def left(hl):
        h, l = hl >> 8, hl & 0xFF
        l = (l - 1) & 0xFF
        if (~l & 0x1F) == 0:
            l = (l + 0x20) & 0xFF
        return (h << 8) | l

    # -- $9A6B: a map pointer as (x, y) in coordinate units -----------------
    @staticmethod
    def coords(hl):
        x = (hl & 0x1F) * 4
        y = (((hl << 3) >> 8) & 0x1F) * 4
        return x & 0xFF, y & 0xFF

    # -- $98BF and $9A04 ----------------------------------------------------
    def is_wall_98bf(self, hl):
        a = self.get(hl) & 0x7F
        return 1 <= a <= 0x10

    def is_wall_9a04(self, hl):
        """Carry of $9A04.  An isolated $10 is reset to 0 on the way past."""
        a = self.get(hl) & 0x7F
        if a == 0:
            return False
        if a < 0x10:
            return True
        if a > 0x10:
            return False
        self.put(hl, self.get(hl) & 0x80)
        return True

    # -- $99B3 --------------------------------------------------------------
    def isolate(self, hl):
        if (self.get(hl) & 0x7F) == 0:
            self.put(hl, self.get(hl) | 0x10)

    # -- $9A7A --------------------------------------------------------------
    def spawn(self, hl):
        t = self.cur
        if t == 0x3F:
            self.player = self.coords(hl)
            return
        if t < 0x40:
            return
        x, y = self.coords(hl)
        typ = ((((t >> 1) & 0x1C) | (t & 3)) << 3) & 0xFF
        self.actors.append((x, y, typ))

    # -- $9960 --------------------------------------------------------------
    def place(self, hl):
        self.spawn(hl)
        was_wall = self.is_wall_98bf(hl)
        a = self.cur
        if a >= 0x3F:
            a = 0
        self.put(hl, a)
        if not was_wall:
            return
        for step, bit in ((self.up, 0x04), (self.right, 0x08),
                          (self.down, 0x01), (self.left, 0x02)):
            h2 = step(hl)
            if self.is_wall_98bf(h2):
                self.put(h2, self.get(h2) & ~bit & 0xFF)
                self.isolate(h2)

    # -- $99BA --------------------------------------------------------------
    def place_wall(self, hl):
        b = 0
        h2 = self.up(hl)
        if self.is_wall_9a04(h2):
            self.put(h2, self.get(h2) | 0x04)
            b |= 0x01
        h2 = self.right(hl)
        if self.is_wall_9a04(h2):
            self.put(h2, self.get(h2) | 0x08)
            b |= 0x02
        h2 = self.down(hl)
        if self.is_wall_9a04(h2):
            b |= 0x04
            self.put(h2, self.get(h2) | 0x01)
        h2 = self.left(hl)
        if self.is_wall_9a04(h2):
            b |= 0x08
            self.put(h2, self.get(h2) | 0x02)
        if b == 0:
            b = 0x10
        self.put(hl, b)
        if self.pen == 0xC0:
            self.put(hl, self.get(hl) | 0x80)

    # -- $9945 --------------------------------------------------------------
    def paint(self, hl):
        a = self.pen
        if a >= 0xC0:
            self.place_wall(hl)
            return
        if a == 0x80:
            self.cur = 0x11
        elif a == 0x40:
            self.cur = 0x12
        else:
            self.cur = 0x36
        self.place(hl)


# --------------------------------------------------------------------------
# $98C6 -- the vector table walker
# --------------------------------------------------------------------------
def tag(b):
    return b & 0xE0


def walk_table(mp, buf, off, count, hl, trace=None):
    """Walk `count` BYTES of table starting at buf[off].  Returns the cursor.

    buf must extend past the table: $9A5C reads one byte beyond the entry it is
    deciding about, and at the end of a record that byte belongs to whatever
    follows.  Reading it is part of the format.
    """
    de = off
    c = count
    while True:
        if c == 0:
            return hl
        b0 = buf[de] if de < len(buf) else 0

        def same(i):
            x = buf[i] if i < len(buf) else 0
            y = buf[i + 1] if i + 1 < len(buf) else 0
            return tag(x) == tag(y)

        run = False
        if c - 1 == 0:                       # $98CB
            run = True
        elif not same(de):                   # $98CF
            run = True
        else:
            if c == 3 and same(de + 1):      # $98D4..$98DE
                run = True
            elif same(de + 1):               # $98E1 fell through
                if not same(de + 2):         # $98E6
                    run = True
        if run:
            # ---- $990E: a RUN ------------------------------------------
            idx = (b0 >> 4) & 0x0E
            delta = DELTA[idx >> 1]
            pen = mp.pen
            if pen < 0xC0 and pen != 0x20:
                mp.pen = 0x80 if (idx & 0x04) else 0x40
            n = (b0 & 0x1F) + 1
            for _ in range(n):
                hl = (hl + delta) & 0xFFFF
                mp.paint(hl)
            if trace is not None:
                trace.append(('run', b0, idx >> 1, n, mp.pen))
            de += 1
            c -= 1
            continue
        # ---- a POINT -----------------------------------------------------
        b1 = buf[de + 1] if de + 1 < len(buf) else 0
        col = b0 & 0x1F
        row = b1 & 0x1F
        hl = 0x8000 + row * 32 + col
        mp.pen = b0 & 0xE0
        mp.paint(hl)
        if trace is not None:
            trace.append(('point', b0, b1, col, row, mp.pen))
        de += 2
        c -= 2
        if c == 0:
            return hl


# --------------------------------------------------------------------------
# $97CB -- the whole expander
# --------------------------------------------------------------------------
def expand(buf, off, trace=None, thin36=None):
    """thin36: which of the $36 cells $9B5F keeps when flags bit 2 is set.
    None leaves them all in place (and .thirtysix lists the candidates)."""
    rec_len = buf[off] + (0x100 if buf[off + 2] & 0x80 else 0)
    flags = buf[off + 1]
    hdrlen = buf[off + 3]

    mp = Map()
    hl = 0x8000
    # $9845 -- the record's own vector table
    hl = walk_table(mp, buf, off + 4, hdrlen, hl, trace)
    # $9855 -- the fixed border at $7DB4, 3 bytes if flags bit 7 else 4
    hl = walk_table(mp, BORDER + b'\x00\x00', 0,
                    3 if (flags & 0x80) else 4, hl, trace)

    # $9859..$986D -- how many RLE bytes
    ninth = bool(buf[off + 2] & 0x80)
    c = (buf[off] - ((hdrlen + 4) & 0xFF)) & 0xFF
    if buf[off] < ((hdrlen + 4) & 0xFF):
        ninth = False
    total = (c if c else 256) + (256 if ninth else 0)

    de = off + 4 + hdrlen
    hl = 0x8000
    left = total
    while left:
        a = buf[de] if de < len(buf) else 0
        if a >= 0x80:                       # $9876 -- skip a run of cells
            hl = (hl + (a & 0x7F) + 1) & 0xFFFF
        elif a >= 0x13:                     # $98AC -- a literal tile
            mp.cur = a
            mp.place(hl)
            hl = (hl + 1) & 0xFFFF
        else:                               # $98B5 -- repeat the current tile
            for _ in range(a + 1):
                mp.place(hl)
                hl = (hl + 1) & 0xFFFF
        de += 1
        left -= 1

    # $989F -- $9B5F: with flags bit 2 set, exactly ONE $36 cell survives and
    # which one is a draw from the game's LD A,R entropy.
    mp.thirtysix = [i for i in range(0x400) if mp.cell[i] == 0x36]
    if (flags & 0x04) and thin36 is not None:
        for i in mp.thirtysix:
            if i != thin36:
                mp.cell[i] = 0

    # $9899 -- $9B0E: partition the actor list so that every record whose type
    # byte has (type & $E0) == $A0 comes first, in order.
    j = 0
    for i in range(len(mp.actors)):
        if (mp.actors[i][2] & 0xE0) == 0xA0:
            mp.actors[i], mp.actors[j] = mp.actors[j], mp.actors[i]
            j += 1

    return mp, dict(rec_len=rec_len, flags=flags, hdrlen=hdrlen,
                    rle_bytes=total, end=off + rec_len)


# --------------------------------------------------------------------------
# $9C06 / $9C69 -- the two mirrors, applied on a level >= 8 when the random
# byte $84C7 has bit 1 / bit 0 set.  Each mirrors the map AND rotates it one
# cell, and fixes the wall connection bits as it goes.
#
# BOTH mirrors END IN THE SAME TAIL, $9C3D..$9C68, and that tail transforms the
# player start AND EVERY ACTOR.  ($9C69 reaches it via $9CA3 LD DE,$7F00 /
# JR $9C3D.)  The tail is
#     $9C3D  HL=$8492:  x := ((x XOR E) + 1) AND $7C ;  y := ((y XOR D) + 1) AND $7C
#     $9C4D  HL=$5C00, B=(IY+$17)=$8496 (the actor count), RET z
#     $9C56  the same two lines per actor, stride 4
# with DE = $007F for the horizontal mirror and $7F00 for the vertical one.
# MEASURED: forcing $84C7 over 0..3 on six records changes the actor list in
# every mirrored orientation, exactly as this tail predicts.
# --------------------------------------------------------------------------
def _is_wall_9ccd(a):
    return a < 0x10 if a < 0x7F else a < 0x90


def _flip_lr(a):                                   # $9CBA
    if not _is_wall_9ccd(a):
        return a
    v = a & 0x85                                   # up, down, bit 7
    if a & 0x08:                                   # left  -> right
        v |= 0x02
    if a & 0x02:                                   # right -> left
        v |= 0x08
    return v


def _flip_ud(a):                                   # $9CA8
    if not _is_wall_9ccd(a):
        return a
    v = a & 0x8A                                   # right, left, bit 7
    if a & 0x04:                                   # down -> up
        v += 1
    if a & 0x01:                                   # up   -> down
        v |= 0x04
    return v


def _mirror_coords(mp, e, d):
    """$9C3D..$9C68 -- the tail both mirrors share.  E is XORed into every x,
    D into every y; the +1 is the one-cell rotation that goes with the flip."""
    if mp.player:
        x, y = mp.player
        mp.player = (((x ^ e) + 1) & 0x7C, ((y ^ d) + 1) & 0x7C)
    for i, a in enumerate(mp.actors):
        mp.actors[i] = ((((a[0] ^ e) + 1) & 0x7C),
                        (((a[1] ^ d) + 1) & 0x7C)) + tuple(a[2:])


def mirror_h(mp):
    """$9C06: reverse every row, then rotate it one cell right."""
    for r in range(32):
        row = [_flip_lr(mp.cell[r * 32 + c]) for c in range(32)]
        row = row[::-1]
        row = [row[(c - 1) % 32] for c in range(32)]
        mp.cell[r * 32:r * 32 + 32] = bytes(row)
    _mirror_coords(mp, 0x7F, 0x00)                 # $9C3A LD DE,$007F


def mirror_v(mp):
    """$9C69: reverse every column, then rotate it one cell down."""
    for c in range(32):
        col = [_flip_ud(mp.cell[r * 32 + c]) for r in range(32)]
        col = col[::-1]
        col = [col[(r - 1) % 32] for r in range(32)]
        for r in range(32):
            mp.cell[r * 32 + c] = col[r]
    _mirror_coords(mp, 0x00, 0x7F)                 # $9CA3 LD DE,$7F00


def print_grid(mp):
    for row in range(32):
        print('%2d ' % row + ' '.join('%02X' % b
                                      for b in mp.cell[row * 32:row * 32 + 32]))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--lengths' in sys.argv:
        for n in range(1, 32):
            p = load_pack(n)
            lens = sub_lengths(p)
            used = [x for x in lens if x]
            print('pack %2d  %5d bytes  %2d sub-blocks  sum %5d  hdr+sum %5d  '
                  '%s' % (n, len(p), len(used), sum(used), HDR + sum(used),
                          used))
        return
    if '--export' in sys.argv:
        import json
        out = []
        for n in range(1, 32):
            pack = load_pack(n)
            lens = sub_lengths(pack)
            starts = [HDR]
            for x in lens:
                starts.append(starts[-1] + x)
            for s, ln in enumerate(lens):
                if not ln:
                    continue
                body = pack[starts[s]:]
                buf = bytearray(FIRST_COPY + SECOND_COPY)
                k = min(len(body), len(buf))
                buf[:k] = body[:k]
                mp, info = expand(buf, 0)
                out.append(dict(pack=n, sub=s, flags=info['flags'],
                                byte2=buf[2] & 0x7F, hdrlen=info['hdrlen'],
                                length=ln,
                                player=list(mp.player) if mp.player else None,
                                actors=[list(a) for a in mp.actors],
                                random36=mp.thirtysix if (info['flags'] & 4)
                                else [],
                                cells=list(mp.cell)))
        path = os.path.join(ROOT, 'build', 'levels.json')
        json.dump(out, open(path, 'w'))
        print('wrote %s: %d levels' % (path, len(out)))
        return
    n = int(args[0]) if args else 1
    rec = int(args[1]) if len(args) > 1 else 0
    pack = load_pack(n)
    buf = record_list(pack, None if n == 1 else (rec, rec, rec))
    offs = record_offsets(buf, 12)
    print('pack %d: record offsets %s' % (n, ['$%03X' % o for o in offs]))
    off = offs[rec] if n == 1 else 0
    mp, info = expand(buf, off)
    print(info)
    print('player start', mp.player, ' actors', len(mp.actors))
    if '--grid' in sys.argv:
        print_grid(mp)


if __name__ == '__main__':
    main()
