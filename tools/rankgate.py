#!/usr/bin/env python3
"""
rankgate.py -- THE FOUR-PAGE RANKED DISPLAY, $8767, pixel for pixel.

WHAT THIS ADDS, and what it does not.  The tables, the insertion sort and the
display were ALREADY ported -- HS_TABLES, hsInsert() and hsDrawPage() -- and
`python tools/fegate.py hiscore` already drives the sort against $86ED over
every interesting case including ties and the millions field.  A comment in
web/template.html claimed all three were "NOT PORTED, deliberately"; it was
stale, and reading it instead of grepping cost this session a whole duplicate
renderer before the mistake showed up.

What was missing is a PICTURE test: nothing compared what hsDrawPage paints
against what $8767 paints.  That is what this tool provides -- the four pages
out of the real Z80, bitmap and attributes, for tools/headless.js to compare
byte for byte.

    python tools/rankgate.py           extract + capture, write build/rank.json
                                       and build/_rank.json
    python tools/rankgate.py show      ...and print each page

THE RECORD, from $86B5's key builder and $86ED's comparison:

    byte 0..2   the three NAME letters, PACKED: $8757 does SUB $40 (so 'A'
                becomes 1) and then rotates two bits of C into the top of the
                byte.  C is ($7F2B+7) << 2 -- the MILLIONS count, NOT the
                character, which is what this session first assumed and what
                the shipped hsKey() had right all along.  C is shifted again
                on each of the three calls, so for a millions count under four
                the significant bits land in the THIRD byte.
    byte 3..5   the SCORE, three bytes of packed BCD, most significant first.

    $86ED compares the top two bits of all three name bytes FIRST and only
    then the full bytes -- so the MILLIONS outrank the six BCD digits, which
    is how a score over 999999 sorts at all.

THE FOUR TABLES, one per character.  $8818 gives the base as $87EA + $3C*B
where B is the table number plus one, so they are $8826, $8862, $889E and
$88DA -- 60 bytes each.
Their shipped contents are ten identical rows per table, and the names are the
authors': BIL, BOB, KEV and ARP, each on 10000.

THE DISPLAY, $8767: $84CB counts the page and $8770 RES 2 keeps it to four.
The heading is the CHARACTER NAME out of the same $7E21 table the level-entry
screen uses, centred on row 1 by $8A08.  Then ten rows from $C087 (row 4), one
per rank:

    rank right-aligned to column 8, a $80 separator at 9, a space, the three
    name letters at 11..13, three spaces, and the score from column 17 with
    leading zeros suppressed.

The capture stops at $9CD7 for the same reason introgate.py does: everything
is drawn by then and $9CD7 only blits and blocks.
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC                              # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
TABLE0, TSTRIDE, ROWS, RECLEN = 0x8826, 0x3C, 10, 6
PAGE_COUNTER = 0x84CB
PENDING = 0x84C6


def shadow(x, y):
    return ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def render_page(page):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    for a in range(0xC000, 0xD800):
        m[a] = 0
    m[PENDING] = 0                       # $86A3 JP z,$8767 -- straight to the
    m[PAGE_COUNTER] = page               # display; $8769 INCs this first
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    regs[PC] = 0x869F
    sp = (regs[12] - 2) & 0xFFFF
    mem[sp] = 0
    mem[sp + 1] = 0
    regs[12] = sp
    n = 0
    while n < 3_000_000:
        pc = regs[PC]
        if pc == 0x9CD7 or pc < 0x4000 or mem[pc] == 0x76:
            break
        ops[mem[pc]]()
        n += 1
    else:
        raise RuntimeError('the ranked display did not reach $9CD7')
    return (list(m[0xC000:0xD800]), list(m[0xD800:0xDB00]), m[PAGE_COUNTER])


# NO INSERTION DIFFERENTIAL HERE.  tools/fegate.py hiscore already drives
# $86ED over ties, the shipped score exactly, one point either side of it and
# the millions field, and reports "the port's sort agrees with $86ED on every
# case".  Duplicating it would be two tools claiming the same ground.


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m

    tables = []
    for t in range(4):
        base = TABLE0 + TSTRIDE * t
        rows = []
        for r in range(ROWS):
            p = base + RECLEN * r
            rec = list(m[p:p + RECLEN])
            rows.append({'name': [b & 0x3F for b in rec[:3]],
                         'cls': [(b >> 6) & 3 for b in rec[:3]],
                         'score': rec[3:]})
        tables.append({'base': base, 'rows': rows})

    doc = {'tables': tables, 'stride': TSTRIDE, 'reclen': RECLEN,
           'rows': ROWS, 'head_row': 1, 'first_row': 4,
           'rank_col': 8, 'sep_col': 9, 'name_col': 11, 'score_col': 17,
           'sep_code': 0x80}
    json.dump(doc, open(os.path.join(ROOT, 'build', 'rank.json'), 'w'))

    pages = {}
    for p in range(4):
        px, at, after = render_page(p)
        pages[str(p)] = {'px': px, 'at': at, 'counter_after': after}
    json.dump(pages, open(os.path.join(ROOT, 'build', '_rank.json'), 'w'))

    for t, tab in enumerate(tables):
        r0 = tab['rows'][0]
        nm = ''.join(chr(0x40 + c) for c in r0['name'])
        sc = ''.join('%02X' % b for b in r0['score'])
        print('table %d @ $%04X  ten rows of %r on %s' % (t, tab['base'], nm, sc))
    print('\ncaptured 4 pages, %d distinct'
          % len({json.dumps(pages[k]['px']) for k in pages}))
    print('wrote build/rank.json and build/_rank.json')

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        hud = json.load(open(os.path.join(ROOT, 'build', 'hud_font.json'),
                             encoding='utf-8'))
        gl = {int(k): tuple(base64.b64decode(v))
              for k, v in hud['font']['glyphs'].items()}
        key = {v: k for k, v in gl.items() if any(v)}
        for k in sorted(pages):
            print('\n=== page %s ===' % k)
            px = pages[k]['px']
            for r in range(24):
                line = ''
                for c in range(32):
                    g = tuple(px[shadow(c * 8, r * 8 + y)] for y in range(8))
                    if not any(g):
                        line += ' '
                    elif g in key:
                        code = key[g]
                        line += chr(code) if 32 <= code < 127 else '.'
                    else:
                        line += '?'
                if line.strip():
                    print('  r%2d |%s|' % (r, line))


if __name__ == '__main__':
    main()
