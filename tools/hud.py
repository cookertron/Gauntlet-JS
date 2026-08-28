#!/usr/bin/env python3
"""
hud.py -- the live HUD: score, health, keys, potions, powers, and the renderer.

Everything printed here is READ OUT OF LIVE RAM or MEASURED by driving the real
Z80 in tools/harness.py.  Nothing is inferred from the artwork.

    python tools/hud.py                    dump the HUD state at level start
    python tools/hud.py --state F.pkl      ... from another saved state
    python tools/hud.py --ocr              also OCR rows 20-23 of the real screen
    python tools/hud.py drain [N]          measure the health drain rate
    python tools/hud.py items              drive every pickup, print the deltas
    python tools/hud.py cadence [N]        which field the HUD redraws each pass
    python tools/hud.py layout             the render layout, resolved to cells

THE STATE (32-byte player block, $8420 player 1 / $8440 player 2)

    +$02,+$03  HEALTH   2-byte BCD, big-endian.  $B7E9 adds (clamps $9999),
                        $B852 subtracts (clamps $0000).  Level start = 2000.
    +$04..+$06 SCORE    3-byte BCD, big-endian, 6 digits.  $B807 adds; on
                        overflow past 999999 it bumps $84C8/$84C9 and wraps.
    +$08       KEYS     plain binary
    +$09       POTIONS  plain binary
    +$0A                countdown set to $8C by map item $18
    +$0B       HUD dirty flags -- the whole redraw protocol:
                 bit0 health changed   bit1 score changed
                 bit2 key gained       bit3 key spent
                 bit4 potion gained    bit5 potion spent
                 bit6 left the level (exit or death) -- ALSO STOPS THE DRAIN
                 bit7 died
    +$0C       LEVEL number (binary), written at $B36E, bumped by the exit
    +$13       character index * 8 -> name + ink  ($B890)
    +$14       POWERS: bits 0..5 = the six icons; bit 7 = DEAD/not playing
    +$15       character selector for the damage table at $7D34
    +$17..$1C  damage table, BCD, copied from $7D34 by $AB6F

GLOBALS
    $8497   frame counter, +1 per video frame in the ISR at $A2A2
    $849F   drain phase: the next value of ($8497 & $C0) that will tick
    $84B8   drain ticks since the last pickup -- the HURRY UP timer ($971B)
    $84C8   player 1 score-wrap count      $84C9  player 2
    $84C0   count of dropped-key piles     $84C1/$84C2  keys owed to p1/p2
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                    # noqa: E402
from filmstrip import run_frames                               # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

P1, P2 = 0x8420, 0x8440
FONT = 0x77B0                    # glyph = FONT + (code-32)*8, 8 rows, 8 px
FRAMES = 0x8497
DRAIN_PHASE = 0x849F
HURRY = 0x84B8

# $B713 / $B722: the screen address of each player's panel, and $948C's.
PANEL = {1: 0x5080, 2: 0x5091}   # top-left of the 15-wide panel  (row 20)
TEXT = {1: 0x50C9, 2: 0x50DA}    # the field origin used by $B70E..$B7B8

FLAGBITS = ['health', 'score', 'key+', 'key-', 'potion+', 'potion-',
            'left-level', 'DEAD']
# $B61F: bit -> (column offset within the panel, attribute)
POWERS = [(0, 0x46), (1, 0x43), (2, 0x44), (12, 0x42), (13, 0x47), (14, 0x45)]
NAMES = {0: ('WARRIOR', 0x42), 8: ('VALKYRIE', 0x45), 16: ('WIZARD', 0x46)}
NAME_DEFAULT = ('ELF', 0x44)


# ---------------------------------------------------------------- decoding --
def bcd(bs):
    """A big-endian BCD run as a decimal string, exactly as the game means it."""
    return ''.join(f'{b:02X}' for b in bs)


def rowcol(addr):
    """Spectrum bitmap address -> (character row, column, pixel row)."""
    o = addr - 0x4000
    return ((o >> 11) * 8 + ((o >> 5) & 7), o & 31, (o >> 8) & 7)


def name_of(m, base):
    return NAMES.get(m[base + 0x13], NAME_DEFAULT)


def read(m, base):
    """Every HUD field of one player block."""
    f = m[base + 0x0B]
    return {
        'health': bcd(m[base + 2:base + 4]),
        'score': bcd(m[base + 4:base + 7]),
        'keys': m[base + 8],
        'potions': m[base + 9],
        'item_timer': m[base + 0x0A],
        'flags': f,
        'flagnames': [n for i, n in enumerate(FLAGBITS) if f & (1 << i)],
        'level': m[base + 0x0C],
        'char_index': m[base + 0x13],
        'name': name_of(m, base)[0],
        'ink': name_of(m, base)[1],
        'powers': m[base + 0x14] & 0x3F,
        'dead': bool(m[base + 0x14] & 0x80),
        'damage': list(m[base + 0x17:base + 0x1D]),
    }


# ------------------------------------------------------------------- layout --
def layout(player=1):
    """The render layout, resolved from the routines to screen cells.

    $B70E picks IX and DE'; DE' is the FIELD ORIGIN.  Every field is placed
    relative to it, so the two players differ only by TEXT[2]-TEXT[1] = $11.
    """
    e = TEXT[player]
    panel = PANEL[player]
    out = []
    r, c, _ = rowcol(panel)
    out.append(('panel', r, c, 15, 'rows 20-22 static text, row 23 blanked'))
    out.append(('name (len 3)', r, c + ((15 - 3 + 1) >> 1), 3,
                '$B60A centres it: col += (15-len+1)>>1'))
    # score: $B748 E-8, $B74E 3 BCD bytes = 6 digits
    r, c, _ = rowcol(e - 8)
    out.append(('score', r, c, 6, '3 BCD bytes, leading zeros -> spaces'))
    r, c, _ = rowcol(e)
    out.append(('health', r, c, 4, '2 BCD bytes'))
    # icons: $B7B8 low byte = E + A + $16
    for k in range(1, 11):
        r, c, _ = rowcol((e & 0xFF00) | ((e + k + 0x16) & 0xFF))
        out.append((f'key {k}', r, c, 1, "char $21, ink 6"))
    for p in range(1, 11):
        r, c, _ = rowcol((e & 0xFF00) | ((e + (16 - p) + 0x16) & 0xFF))
        out.append((f'potion {p}', r, c, 1, 'char $22, ink 5'))
    for bit, (col, attr) in enumerate(POWERS):
        r, c, _ = rowcol(panel + col)
        out.append((f'power bit{bit}', r, c, 1, f'ATTRIBUTE ONLY, ink ${attr:02X}'))
    return out


def print_layout():
    for player in (1, 2):
        print(f'--- player {player}: panel ${PANEL[player]:04X}, '
              f'field origin ${TEXT[player]:04X} ---')
        print('  field            row col width  note')
        for nm, r, c, w, note in layout(player):
            print(f'  {nm:15}  {r:3} {c:3} {w:5}  {note}')
        print()


# ---------------------------------------------------------------------- ocr --
def ocr(m, rows=range(20, 24)):
    """Read the HUD back off the real screen through the game's own font."""
    glyphs = {}
    for code in range(32, 128):
        g = bytes(m[FONT + (code - 32) * 8: FONT + (code - 32) * 8 + 8])
        glyphs.setdefault(g, code)
    for row in rows:
        line = ''
        for col in range(32):
            o = row * 32 + col
            addr = 0x4000 + ((o & 0x300) << 3) + (o & 0xFF)
            cell = bytes(m[addr + 256 * pr] for pr in range(8))
            code = glyphs.get(cell)
            line += chr(code) if code and 32 <= code < 127 else (
                '.' if not any(cell) else '?')
        attrs = ''.join(f'{m[0x5800 + row * 32 + c]:02X}' for c in range(32))
        print(f'  row {row} |{line}|')
        print(f'          {attrs}')


# --------------------------------------------------------------------- dump --
def dump(state=STATE, want_ocr=False):
    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    m = h.memobj.m
    print(f'=== HUD state from {os.path.relpath(state, ROOT)} ===')
    for n, base in ((1, P1), (2, P2)):
        d = read(m, base)
        print(f'\nplayer {n}  (block ${base:04X})')
        print(f'  character    {d["name"]}  (+$13 = {d["char_index"]}, '
              f'ink ${d["ink"]:02X})   {"DEAD/absent" if d["dead"] else "playing"}')
        print(f'  SCORE        {d["score"]}      (${base+4:04X}..${base+6:04X}, 3-byte BCD)')
        print(f'  HEALTH       {d["health"]}        (${base+2:04X}/${base+3:04X}, 2-byte BCD)')
        print(f'  KEYS         {d["keys"]}           (${base+8:04X})')
        print(f'  POTIONS      {d["potions"]}           (${base+9:04X})')
        print(f'  powers       ${d["powers"]:02X} bits ' +
              (','.join(str(b) for b in range(6) if d['powers'] & (1 << b)) or '-') +
              f'   (${base+0x14:04X})')
        print(f'  level        {d["level"]}           (${base+0x0C:04X})')
        print(f'  dirty flags  ${d["flags"]:02X} ' +
              (' '.join(d['flagnames']) or '(clean)') + f'   (${base+0x0B:04X})')
        print(f'  item timer   ${d["item_timer"]:02X}          (${base+0x0A:04X})')
        print('  damage tbl   ' + ' '.join(f'{b:02X}' for b in d['damage']) +
              f'   (${base+0x17:04X}.., BCD, from $7D34)')
    print('\nglobals')
    print(f'  $8497 frame counter      ${m[FRAMES]:02X}   (+1 per video frame, ISR $A2A2)')
    print(f'  $849F drain phase        ${m[DRAIN_PHASE]:02X}   (ticks when ($8497&$C0) == this)')
    print(f'  $84B8 hurry-up timer     ${m[HURRY]:02X}   (drain ticks since the last pickup)')
    print(f'  $84C8/$84C9 score wraps  ${m[0x84C8]:02X}/${m[0x84C9]:02X}')
    print(f'  $84C0/$84C1/$84C2 piles  ${m[0x84C0]:02X}/${m[0x84C1]:02X}/${m[0x84C2]:02X}')
    print(f'  $8491 pass counter       ${m[0x8491]:02X}   '
          f'(&3 = {m[0x8491] & 3}: 0 p1 health, 1 p1 score, 2 p2 health, 3 p2 score)')
    if want_ocr:
        print('\nthe HUD as the ORIGINAL has it on screen (rows 20-23, its own font):')
        ocr(m)
    return h


# ---------------------------------------------------------------- measuring --
def measure_drain(passes=200, state=STATE):
    """Passes and frames per BCD point of the passive health drain."""
    st = pickle.load(open(state, 'rb'))
    h = Harness(); h.load_state(st); m = h.memobj.m
    drops, last = [], None
    for i in range(passes):
        run_frames(h, 4)
        hp = (m[0x8422] << 8) | m[0x8423]
        if last is not None and hp != last:
            drops.append((i + 1, m[0x8491], m[FRAMES], m[DRAIN_PHASE], m[HURRY], hp))
        last = hp
    print(f'{len(drops)} drain ticks in {passes} passes')
    print(' pass  $8491 $8497 $849F $84B8  health')
    for p, ctr, fr, ph, hu, hp in drops[:12]:
        print(f'  {p:4}    {ctr:02X}    {fr:02X}    {ph:02X}    {hu:02X}   {hp:04X}')
    gaps = [drops[i + 1][0] - drops[i][0] for i in range(len(drops) - 1)]
    print(f'gaps in passes: {sorted(set(gaps))}   ({len(gaps)} intervals)')

    h2 = Harness(); h2.load_state(st); m2 = h2.memobj.m
    fdrops, last = [], None
    for i in range(passes * 4):
        run_frames(h2, 1)
        hp = (m2[0x8422] << 8) | m2[0x8423]
        if last is not None and hp != last:
            fdrops.append(i)
        last = hp
    fg = [fdrops[i + 1] - fdrops[i] for i in range(len(fdrops) - 1)]
    print(f'gaps in video frames: {sorted(set(fg))}   -> '
          f'{sorted(set(fg))[0] / 50.08:.3f} s per point of health')


ITEMS = [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B,
         0x1C, 0x1D, 0x1E, 0x1F, 0x2F, 0x30, 0x31, 0x32, 0x36]
PROBE_CELL = 0x8044          # the cell immediately right of the player


def measure_items(passes=14, state=STATE):
    """Plant one map value in the player's path, hold right, print the deltas."""
    st = pickle.load(open(state, 'rb'))

    def one(val):
        h = Harness(); h.load_state(st); m = h.memobj.m
        m[0x804A] = 0                     # the room's own treasure, out of the way
        if val is not None:
            m[PROBE_CELL] = val
        b = read(m, P1)
        sel, bit = KM['D']
        h.ports.press(sel, keymask(bit))
        for _ in range(passes):
            run_frames(h, 4)
        a = read(m, P1)
        return b, a, m[PROBE_CELL]

    def dec(s):
        return int(s)

    b0, a0, _ = one(None)
    base_hp = dec(a0['health']) - dec(b0['health'])
    print(f'baseline (empty cell, {passes} passes): health {b0["health"]} -> '
          f'{a0["health"]} (the passive drain), score {a0["score"]}')
    print('\nval  dscore  dhealth  keys pot powers +$0A  cell after')
    for v in ITEMS:
        b, a, cell = one(v)
        ds = dec(a['score']) - dec(b['score'])
        dh = dec(a['health']) - dec(b['health']) - base_hp
        print(f'${v:02X} {ds:+7} {dh:+8}   {a["keys"]}   {a["potions"]}   '
              f'${a["powers"]:02X}    ${a["item_timer"]:02X}   ${cell:02X}')


def measure_cadence(passes=10, state=STATE):
    """Which HUD field each pass redraws, from the glyph writer's own writes."""
    st = pickle.load(open(state, 'rb'))
    for label, keys, pot, bits in (('health dirty', 0, 0, 0x01),
                                   ('score dirty', 0, 0, 0x02),
                                   ('key gained (3 keys)', 3, 2, 0x04),
                                   ('key spent (3 keys)', 3, 2, 0x08),
                                   ('potion gained (2)', 3, 2, 0x10),
                                   ('potion spent (2)', 3, 2, 0x20)):
        h = Harness(); h.load_state(st); m = h.memobj.m
        m[0x8428], m[0x8429], m[0x842B] = keys, pot, bits
        cells = []
        for p in range(passes):
            h.memobj.watch(0x4000, 0x5B00)
            run_frames(h, 4)
            h.memobj.unwatch()
            for pc, a, _v in h.memobj.log:
                if pc == 0xB6D1:                       # the glyph writer
                    r, c, _ = rowcol(a)
                    cells.append((p + 1, 'chr', r, c))
                elif pc == 0xB7C4:                     # the icon's attribute
                    o = a - 0x5800
                    cells.append((p + 1, 'att', o // 32, o % 32))
        seen = sorted(set(cells))
        print(f'  {label:22} -> ' +
              ', '.join(f'p{p}:{k} r{r}c{c}' for p, k, r, c in seen))


def main():
    args = sys.argv[1:]
    state = STATE
    if '--state' in args:
        i = args.index('--state'); state = args[i + 1]; del args[i:i + 2]
    want_ocr = '--ocr' in args
    if want_ocr:
        args.remove('--ocr')
    cmd = args[0] if args else 'dump'
    n = int(args[1]) if len(args) > 1 else None
    if cmd == 'dump':
        dump(state, want_ocr)
    elif cmd == 'drain':
        measure_drain(n or 200, state)
    elif cmd == 'items':
        measure_items(n or 14, state)
    elif cmd == 'cadence':
        measure_cadence(n or 10, state)
    elif cmd == 'layout':
        print_layout()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
