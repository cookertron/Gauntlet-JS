#!/usr/bin/env python3
"""
extract.py -- pull the engine's assets out of LIVE RAM in the harness.

Manual Q7: "FOR ANYTHING PACKED, THE PRIMARY PATH IS TO READ THE LEVELS OUT OF
THE LIVE BUFFER in the simulator."  Gauntlet's dungeon packs are variable-length
record lists expanded at load time, so that is the route taken here.

WHAT IS RECOVERED, AND FROM WHERE
---------------------------------
map        the 32x32 grid at $8000..$83FF.  Address arithmetic read out of the
           cursor routines: $9A2D steps right (INC L / AND $1F wraps the
           column), $9A3D steps down (+$20), $9A18 steps up (-$20, with H
           wrapping $7F -> $83).  So rows are 32 bytes and the grid wraps in
           both axes.  One cell is 4 coordinate units (the step routines at
           $9A27/$9A37 add 4), and 4 units is 16 screen pixels.
tiles      the 16x16 pixel block each map value actually draws, sampled out of
           the ORIGINAL's own shadow screen at the position the camera
           equations put it.  Empirical, and verified by re-rendering the whole
           playfield from the map and diffing against the shadow screen.
player     the player sprite, isolated as (real screen AND NOT shadow screen)
           at the player's own computed screen position.
hud        rows 20-23 of the real screen, which the shadow buffer does not hold
           (measured: rows 0-19 agree between the two buffers, rows 20-23 are
           entirely different -- that is the playfield/HUD boundary).
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                    # noqa: E402
from keyprobe import KEYS, keymask                              # noqa: E402
from filmstrip import run_frames                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

MAP_BASE = 0x8000            # $9A18/$9A2D/$9A3D
MAP_W = MAP_H = 32
CELL_PX = 16                 # 4 coordinate units x 4 px per unit
UNITS_PER_CELL = 4
PX_PER_UNIT = 4

P_X, P_Y, P_DIR = 0x8420, 0x8421, 0x8427
CAM_X, CAM_Y = 0x848B, 0x848C
PLAYFIELD_ROWS = 20          # rows 0-19; 20-23 are the HUD

SHADOW_BMP, SHADOW_ATTR = 0xC000, 0xD800
REAL_BMP, REAL_ATTR = 0x4000, 0x5800

# player 1's direction keys, found behaviourally (tools/keyprobe.py)
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def scr(base, x, y):
    """Spectrum display-file address (manual 3.2)."""
    return base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def read_block(m, bmp_base, attr_base, sx, sy, w=16, h=16):
    """16 rows x 2 bytes of bitmap plus the 2x2 attribute patch."""
    rows = []
    for yy in range(h):
        rows.append([m[scr(bmp_base, sx + b * 8, sy + yy)] for b in range(w // 8)])
    attrs = []
    for ry in range(h // 8):
        for rx in range(w // 8):
            attrs.append(m[attr_base + ((sy // 8) + ry) * 32 + (sx // 8) + rx])
    return rows, attrs


def extract_tiles(m, cam_x, cam_y, grid):
    """One 16x16 block per distinct map value, sampled where that value is
    actually on screen.  First occurrence wins."""
    tiles = {}
    for r in range(MAP_H):
        for c in range(MAP_W):
            v = grid[r][c]
            if v in tiles:
                continue
            sx = c * CELL_PX - cam_x * PX_PER_UNIT
            sy = r * CELL_PX - cam_y * PX_PER_UNIT
            if sx < 0 or sy < 0 or sx + 16 > 256 or sy + 16 > PLAYFIELD_ROWS * 8:
                continue
            rows, attrs = read_block(m, SHADOW_BMP, SHADOW_ATTR, sx, sy)
            tiles[v] = {'bitmap': [b for row in rows for b in row], 'attrs': attrs}
    return tiles


def verify_tiles(m, cam_x, cam_y, grid, tiles):
    """Re-render the playfield from the map using the extracted tiles and diff
    it against the original's own shadow screen.  This is the closure test for
    the tile extraction: if the map and the tile set are right, the two agree."""
    diff = same = missing = 0
    per_value = {}                   # value -> [agree, differ]
    for r in range(MAP_H):
        for c in range(MAP_W):
            sx = c * CELL_PX - cam_x * PX_PER_UNIT
            sy = r * CELL_PX - cam_y * PX_PER_UNIT
            if sx < 0 or sy < 0 or sx + 16 > 256 or sy + 16 > PLAYFIELD_ROWS * 8:
                continue
            v = grid[r][c]
            t = tiles.get(v)
            if t is None:
                missing += 1
                continue
            rows, _ = read_block(m, SHADOW_BMP, SHADOW_ATTR, sx, sy)
            flat = [b for row in rows for b in row]
            pv = per_value.setdefault(v, [0, 0])
            for i, b in enumerate(flat):
                if b == t['bitmap'][i]:
                    same += 1
                    pv[0] += 1
                else:
                    diff += 1
                    pv[1] += 1
    return same, diff, missing, per_value


def extract_player_sprite(m, px, py, cam_x, cam_y):
    """real AND NOT shadow at the player's own screen position: the pixels the
    player sprite ADDED to the background."""
    sx = (px - cam_x) * PX_PER_UNIT
    sy = (py - cam_y) * PX_PER_UNIT
    out = []
    for yy in range(16):
        for b in range(2):
            real = m[scr(REAL_BMP, sx + b * 8, sy + yy)]
            shad = m[scr(SHADOW_BMP, sx + b * 8, sy + yy)]
            out.append(real & (~shad & 0xFF))
    ink = m[REAL_ATTR + (sy // 8) * 32 + (sx // 8)]
    return out, ink, sx, sy


def measure_dirbits(st):
    out = {}
    for name, k in DIRKEY.items():
        h = Harness()
        h.load_state(st)
        sel, bit = KM[k]
        h.ports.press(sel, keymask(bit))
        run_frames(h, 8)
        out[name] = h.memobj.m[P_DIR]
    return out


def sweep_tiles(st, grid, step=16, settle=55):
    """Walk the camera over the whole 128x128-unit world by POKING the player's
    coordinates and letting the game move its own camera and re-render its own
    shadow screen (manual 6.8: construct the scenario by poking).  Sample every
    map value that comes into view.

    Sampling from only the opening view covers 3 of the 19 values present, so a
    closure test over that view alone certifies almost nothing -- this is what
    makes the gate mean something."""
    from collections import Counter
    votes = {}                       # map value -> Counter of (bitmap, attrs)
    views = []
    checked = 0
    h = Harness()
    # WALK the camera rather than teleporting it.  Two earlier attempts were
    # both wrong, and the closure test caught both:
    #   - poking only the player left the camera tens of passes behind (it
    #     steps 2 units per pass), so most views still showed the opening room
    #     and only 9 of the 19 map values were ever seen;
    #   - poking the camera as well jumped it, but the game only redraws newly
    #     exposed columns, so the shadow screen stayed STALE and the closure
    #     test fell to 93.14% -- a contaminated gate, not a better one.
    # Hopping the player a short distance and letting the camera catch up keeps
    # the shadow screen coherent, which is what the gate depends on.
    for py in range(0, 128, step):
        for px in range(0, 128, step):
            h.load_state(st)
            h.poke(P_X, px)
            h.poke(P_Y, py)
            # The camera steps only 2 units per pass, so it needs tens of
            # passes to converge after the player is moved -- and it must be
            # allowed to CONVERGE ON ITS OWN, because the game redraws the
            # shadow screen as it scrolls.  Poking the camera instead jumps it
            # past that redraw and leaves the buffer stale: that attempt scored
            # 93.14% on the closure test against 99.89% here, which is how it
            # was caught.
            run_frames(h, 4 * settle)
            m = h.memobj.m
            cx, cy = m[CAM_X], m[CAM_Y]
            views.append((bytes(m[SHADOW_BMP:SHADOW_BMP + 0x1800]),
                          bytes(m[SHADOW_ATTR:SHADOW_ATTR + 0x300]), cx, cy))
            for k, v in extract_tiles(m, cx, cy, grid).items():
                votes.setdefault(k, Counter())[
                    (tuple(v['bitmap']), tuple(v['attrs']))] += 1
            checked += 1
        print(f'  swept y={py:3d}: {len(votes)} distinct tile values so far')

    # A tile sampled where an ACTOR happened to stand is contaminated, and a
    # first-occurrence sample keeps whichever one it met first.  Take the MODAL
    # block per value instead: the background is what the value looks like most
    # of the time.
    tiles = {}
    contested = {}
    for k, c in votes.items():
        (bm, at), n = c.most_common(1)[0]
        tiles[k] = {'bitmap': list(bm), 'attrs': list(at)}
        if len(c) > 1:
            contested[k] = (n, sum(c.values()), len(c))

    # re-verify against every view we captured
    same_t = diff_t = 0
    per_value_total = {}
    for bmp, attr, cx, cy in views:
        class V:
            def __getitem__(self, i):
                if SHADOW_BMP <= i < SHADOW_BMP + 0x1800:
                    return bmp[i - SHADOW_BMP]
                if SHADOW_ATTR <= i < SHADOW_ATTR + 0x300:
                    return attr[i - SHADOW_ATTR]
                return 0
        s, d, _, pv = verify_tiles(V(), cx, cy, grid, tiles)
        same_t += s
        diff_t += d
        for k, (a, b) in pv.items():
            acc = per_value_total.setdefault(k, [0, 0])
            acc[0] += a; acc[1] += b
    if contested:
        print('  values whose block was not unanimous across views '
              '(modal one kept):')
        for k, (n, tot, variants) in sorted(contested.items()):
            print(f'    ${k:02X}: modal in {n}/{tot} samples, {variants} variants')
    return tiles, same_t, diff_t, checked, per_value_total


def settled_anim(st):
    """$8499 and $847E ONE MAIN-LOOP PASS ON from the capture.

    The capture is anchored MID-PASS at PC=$ABA1, inside the actor walk and
    before $ABBC, so the raw bytes ($8499 = 0, $847E bit 1 set) describe a
    moment the engine's seed() does not correspond to: every differential in
    this project settles with one step_to_loop_top() first, and that settle
    CONSUMES the pending flag.  Sampling here instead is what puts the port's
    animation on the same phase as the tables it is compared against -- taking
    the raw pair instead put the actor walk cycle a whole pass late.
    """
    import sim_move                                            # noqa: E402
    hh = Harness()
    hh.load_state(st)
    sim_move.step_to_loop_top(hh)
    mm = hh.memobj.m
    return mm[0x8499], mm[0x847E]


def main():
    st = pickle.load(open(STATE, 'rb'))
    h = Harness()
    h.load_state(st)
    m = h.memobj.m
    anim_ctr, anim_flags = settled_anim(st)

    grid = [[m[MAP_BASE + r * 32 + c] for c in range(32)] for r in range(32)]
    cam_x, cam_y = m[CAM_X], m[CAM_Y]
    px, py = m[P_X], m[P_Y]

    tiles, same, diff, views, per_value = sweep_tiles(st, grid)
    missing = 0
    print(f'swept {views} camera positions')
    total = same + diff
    pct = 100.0 * same / total if total else 0.0
    print(f'tile closure test: {same}/{total} bitmap bytes agree ({pct:.2f}%), '
          f'{diff} differ, {missing} cells had no tile')

    sprite, sprite_ink, sx, sy = extract_player_sprite(m, px, py, cam_x, cam_y)
    hud_bmp = []
    for y in range(PLAYFIELD_ROWS * 8, 192):
        for xb in range(32):
            hud_bmp.append(m[scr(REAL_BMP, xb * 8, y)])
    hud_attr = [m[REAL_ATTR + r * 32 + c]
                for r in range(PLAYFIELD_ROWS, 24) for c in range(32)]

    assets = {
        '_source': 'read out of live RAM in tools/harness.py; see notes/',
        'map': {
            'base': MAP_BASE, 'w': MAP_W, 'h': MAP_H,
            'units_per_cell': UNITS_PER_CELL, 'px_per_unit': PX_PER_UNIT,
            'wraps': True,
            'grid': [row[:] for row in grid],
        },
        'tiles': {str(k): v for k, v in sorted(tiles.items())},
        'tile_closure': {'same': same, 'total': total, 'pct': round(pct, 2),
                         'missing_cells': missing},
        'player': {
            'x': px, 'y': py, 'cam_x': cam_x, 'cam_y': cam_y,
            'sprite': sprite, 'ink': sprite_ink,
            'keys': m[0x8428],          # (IX+8), the key count tested at $A8F2
            'block': list(m[0x8420:0x8440]),
            # PLAYER 2'S BLOCK, verbatim.  It is TAPE DATA -- a write-watch
            # over a whole cold boot logs no write that CHANGES $844B or
            # $8454 -- and it ships marked "not in the game": +$0B = $C0,
            # +$14 = $80, +$0F = $E8 (sprite base 232), (x,y) = (0,0),
            # health 0000.  The port seeds him from it rather than from
            # constants, so "he starts outside the game" is the tape's
            # statement and not this file's.
            'block2': list(m[0x8440:0x8460]),
        },
        'globals': {
            # $8491 (IY+$12), the PASS COUNTER, incremented once per main-loop
            # pass at $9CFB (reached from $8550).  It drives the actor round
            # robin at $AC0E and the player's walk counter at $A5DC, so the
            # port has to be SEEDED with it rather than starting from zero.
            'pass_ctr': m[0x8491],
            # $84B3, the CLASS-5 POTION BOUNTY.  $8BF9 LD D,(IY+$34) makes it
            # the HUNDREDS added to the thrower's score for every actor with
            # state >= $A0 that the sweep removed.  It is written at exactly
            # one place, $8D83 -- and NEVER initialised at level start -- so
            # the value a level begins with is whatever the last roll left.
            # Extracted rather than assumed for the same reason pass_ctr is:
            # `python tools/potiongate.py swarm-hi` prints $6000 of score for
            # six class-5 kills, which is 6 x this byte in the hundreds.
            'potion_bounty': m[0x84B3],
            # $8499 and $847E -- the ACTOR ANIMATION's own clock.  $ABCE
            # reloads $8499 with 7 and the frame interrupt at $A2DA counts it
            # down, setting $847E bit 1 when it reaches zero; the actor pass
            # then consumes bit 1 into bit 2, which is what $ACCE gates the
            # walk cycle on.  So the animation is FRAME-clocked, not
            # pass-clocked, and it slips by a pass wherever a pass costs five
            # frames instead of four.  Extracted so the port starts on the
            # captured phase rather than guessing it.
            'anim_ctr': anim_ctr,
            'anim_flags': anim_flags,
            # $8422/$8423, the player's health, BCD
            'hp': (m[0x8422] << 8) | m[0x8423],
            # $8437..$8439 = (IX+$17..$19): the damage a class-0 actor does to
            # the player, indexed by its tier ($A627/$AF49).  BCD 10/20/30.
            'ghost_damage': [m[0x8437], m[0x8438], m[0x8439]],
            # $8418..$841F, the facing -> direction-mask table ($ADF8/$AA5E)
            'dirmask': list(m[0x8418:0x8420]),
            # $8460..$8468, the wall-follow deflection table ($AC52)
            'deflect': list(m[0x8460:0x8469]),
            # $8469..$8479, the SQUARE table $B246 scores teleport pads with:
            # score = tbl[|dx|>>2] + tbl[|dy|>>2], read via $B258 LD H,$84 /
            # ADD A,$69 / LD L,A.  The bytes are 0,1,4,9,16,25,36,49,64,81,
            # 100,121 then 169,196,225,254 -- n^2 for n = 0..11 and then
            # 13^2,14^2,15^2,254: 144 IS MISSING, so index 12 reads 13^2.  The
            # 17th byte ($8479) is 0 and is reached by an axis difference of
            # 64..67 coordinate units, i.e. exactly half the world.  Dumped,
            # not written out, so the gap is the tape's and not this file's.
            'tele_sq': list(m[0x8469:0x847A]),
        },
        'objects': {
            # the actor scan list read by $A97F: 4-byte stride, count at $8496.
            # This is NOT the map (that is the 32x32 grid at $8000) -- it is the
            # list of live actors, and it is the second gate on a move.
            'base': 0x5C00, 'stride': 4, 'count': m[0x8496],
            'fields': ['x', 'y', 'type', 'flags'],
            'list': [[m[0x5C00 + i * 4 + k] for k in range(4)]
                     for i in range(m[0x8496])],
        },
        'hud': {
            'rows': PLAYFIELD_ROWS,
            'bitmap': base64.b64encode(bytes(hud_bmp)).decode(),
            'attrs': base64.b64encode(bytes(hud_attr)).decode(),
        },
        'background': {
            'bitmap': base64.b64encode(bytes(m[SHADOW_BMP:SHADOW_BMP + 0x1800])).decode(),
            'attrs': base64.b64encode(bytes(m[SHADOW_ATTR:SHADOW_ATTR + 0x300])).decode(),
        },
        'dirbits': measure_dirbits(st),
    }
    out = os.path.join(ROOT, 'build', 'assets.json')
    json.dump(assets, open(out, 'w'), indent=1)
    vals = sorted(set(v for row in grid for v in row))
    print(f'map values present: ' + ' '.join(f'${v:02X}' for v in vals))
    print(f'tiles extracted: {len(tiles)} of {len(vals)} values')
    print(f'player at ({px},{py}) camera ({cam_x},{cam_y}) -> screen ({sx},{sy})')
    print(f'direction bits: ' + ' '.join(f'{k}=${v:02X}' for k, v in assets["dirbits"].items()))
    print('\nper-value closure (attribution of the remainder):')
    for k in sorted(per_value):
        a, b = per_value[k]
        tot = a + b
        print(f'  ${k:02X}: {a}/{tot} agree ({100.0*a/tot:5.1f}%)' + ('   <-- CONTESTED' if b else ''))
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
