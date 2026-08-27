#!/usr/bin/env python3
"""
playersprite.py -- extract the player's animation frames by DECODING the
sprite records, and prove the decode against the running original.

=============================================================================
WHY THE OLD VERSION WAS WRONG, AND WHY THE SPRITE WAS A SPECKLED BLOB
=============================================================================

The blob was never a decoding problem.  It was a BOOT problem, and the
original itself draws the same blob in our captured state.

    $BE20  LDIR $C000 -> $5F00, $1080 bytes
           ; $5F00..$6F7F is now a MASTER TABLE of FOUR character sets,
           ; $420 = 1056 bytes each = 32 records of 33.
    $BE53  LD A,($FFFF)      ; player 1's character index, left by the LOADER
    $BE5A  CALL $BEE5
    $BE64  LD A,($FFFE)      ; player 2's
    $BE74  LD DE,$5F00 / POP HL / LD BC,$0840 / LDIR   ; chosen sets -> $5F00
    $BEE5  LD E,A / SUB A / LD HL,$5F00 / LD IX,$BF19 / LD BC,$0420
           CP E / JR z / INC A / ADD HL,BC / INC IX / INC IX / JR   ; HL += n*$420

The harness never runs the loader's menu, so $FFFF/$FFFE hold a stale $2A.
`ADD HL,$420` then runs 42 times, HL wraps to $0C40 -- inside the 48K ROM --
and 1,056 bytes of ROM code become "the player's sprites".  Measured:
mem[$5F00:$6320] in build/live_cs.bin is a byte-exact copy of ROM $0C40.
Capturing that off the screen captured the original's own garbage, which is
exactly what every "speckled figure" was.  tools/fixchar.py repairs it from
the tape; this tool calls it.

=============================================================================
THE GEOMETRY -- read from the blitter, then measured
=============================================================================

$9DD2 (the 16x16 blitter), disassembled to its terminating JP (IX) at $9E49:

    $9DD2  LD A,H / RRA x3 / AND 3 / ADD A,$D8      attribute address.  Note
                                                    the AND 3: the base is
                                                    ALWAYS $D800, whatever HL
                                                    points at.
    $9DDD  LD (HL),C / INC L / LD (HL),C
    $9DE0  LD A,L / ADD A,$1F / LD L,A / JP nc / INC H
    $9DE8  LD (HL),C / INC L / LD (HL),C            => a 2x2 ATTRIBUTE BLOCK
    $9DEC..$9E12   8 x (POP DE + two writes + INC H)      8 pixel rows
    $9E13  LD A,H / SUB 7 / LD A,L / ADD A,$20 ...  next character row
    $9E22..$9E48   8 x (POP DE + two writes + INC H)      8 more rows
    $9E49  JP (IX)

SIXTEEN POP DE, not eight -- NOTES-render.md stopped counting at the carry
branch at $9E1B, which is the mid-routine character-row step, and that
miscount is the whole origin of "the sprite encoding is not closed".  Both
POP variants ($9DEC LD (HL),E / INC L / LD (HL),D and $9DF1 LD (HL),D /
DEC L / LD (HL),E) put E in the LEFT column and D in the RIGHT, so the byte
order is plain.  32 bitmap bytes, 2 per row, 16 rows, row-major, MSB first,
and the write is OPAQUE -- the sprite REPLACES the background, no mask, no
OR, no pre-shift (there is not one shift instruction in $9DD2..$9E49).

The caller states the record layout outright:

    $A23E  LD C,(HL)        ; record+0 is the ATTRIBUTE
    $A23F  INC HL
    $A241  LD SP,HL         ; record+1 is where the 32 bitmap bytes start
    $A243  JP $9DD2

    ONE RECORD = 33 BYTES = $21.  The old "$42 stride" was two records.

Records are reached through a 255-entry table of 16-bit pointers at $7B00,
indexed (id-1)*2 ($A231 DEC A / ADD A,A / LD L,A / LD H,$7B).  This tool
walks that table rather than assuming the bank is contiguous.

=============================================================================
FRAME SELECTION -- read from $A47B..$A4B2 and $A5D8, then measured
=============================================================================

    $A47B  LD A,(IX+7) / AND 15 / JR z,$A48D    ; direction bits; ZERO keeps
    $A480  LD L,A / LD H,0 / LD DE,$7D0C        ; the PERSISTED (IX+13)
    $A487  ADD HL,DE / LD A,(HL) / LD (IX+13),A
    $A496  LD A,(IX+13)
    $A49F  BIT 6,(IX+14) / JR z / ADD A,8
    $A4A7  BIT 7,(IX+14) / JR z / ADD A,8
    $A4AF  ADD A,(IX+15) / JP $A1DA

        sprite id = (IX+15) + (IX+13) + 8*phase
        (IX+15) = 208 for player 1 ($8420+15), 232 for player 2 ($8440+15)

$7D0C..$7D1B = 90 00 04 00 06 07 05 06 02 01 03 02 00 00 04 00, indexed by
(IX+7)&15 with direction bits 1=up 2=down 4=left 8=right, giving eight
compass SLOTS: 0 N, 1 NE, 2 E, 3 SE, 4 S, 5 SW, 6 W, 7 NW.

The phase counter, $A5D8:

    LD A,E / OR A / JR z          ; no animation while standing still
    BIT 0,(IY+$12) / JR z         ; IY=$847F, so $8491 = the PASS COUNTER:
                                  ; the counter advances on ODD passes only
    BIT 4,(IX+14) / JR nz
    LD A,(IX+14) / ADD A,$40 / LD (IX+14),A    ; a 2-bit counter in bits 7:6

decoded by $A49F/$A4A7 as phase 0 ($00), 1 ($40), 0 ($80), 2 ($C0) -- so the
walk cycle is 0,1,0,2 with each phase held for two passes.

=============================================================================
THE DRAW ORIGIN -- the "-8" IS NOT REAL
=============================================================================

$B557 is the whole coordinate transform and contains no fudge at all:

    LD A,C / SUB (IY+12) / AND $7E / RRA / LD L,A     col = ((x-cam_x)&$7E)>>1
    LD A,B / SUB (IY+13) / AND $7E / RRA / LD H,A     row = ((y-cam_y)&$7E)>>1

Sampling the player's coordinate at the instant PC reaches $9DD2 (rather than
at the end of the pass) gives delta (0,0) on every sample, 5 directions x 24
passes.  The old -8 was a SAMPLING lag, not a draw offset: one harness pass is
four video frames and that window does not begin at the game's main-loop top,
so the coordinate in RAM at the end of it is one 2-unit step = 8 px ahead of
the position that pass actually drew.  Holding DOWN the same artefact lands on
y, and it vanishes once the camera tracks (then coord - cam is constant) --
neither of which a constant -8 on x can explain.  tools/playercompare.py
prints the original's blit address and the port's side by side.

=============================================================================

Usage:  python tools/playersprite.py [--char 3] [--passes 24] [--no-gate]

Writes build/player_frames.json and build/player_frames.png.  Manual G7: the
contact sheet is meant to be LOOKED AT.
"""
import base64
import json
import os
import pickle
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, H, L, C, TAPE_CALL_PC   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402
import fixchar                                                       # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FRAME_T = 69888

PTR_TABLE = 0x7B00           # id -> record pointer, index (id-1)*2   ($A231)
DIRTAB = 0x7D0C              # (IX+7)&15 -> compass slot              ($A480)
REC = 33                     # $21 bytes per record                   ($A23E)
P1, P2 = 0x8420, 0x8440
CAM_X, CAM_Y = 0x848B, 0x848C
SHADOW = 0xC000

BLIT16 = 0x9DD2              # the 16x16 blitter
BLIT16_END = 0x9E49          # its JP (IX)

# The eight compass slots of $7D0C, in slot order.  Slot n's records are
# n + 8*phase, so a set is 24 walk records; records 24..31 are not walk frames.
SLOTNAME = ['up', 'upright', 'right', 'downright',
            'down', 'downleft', 'left', 'upleft']
DIRBIT = {'up': 1, 'down': 2, 'left': 4, 'right': 8}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
# player 2's half of the keyboard, measured by tools/deathgate.py: 8 up,
# I down, K left, L right, M fire, SPACE shift.  These are the arms of
# $8657, which is the "else" method $8589 falls through to.
DIRKEY2 = {'up': '8', 'down': 'I', 'left': 'K', 'right': 'L'}


# --------------------------------------------------------------------------
# decode -- 33 bytes in, one 16x16 frame out
# --------------------------------------------------------------------------
def record_ptr(mem, sid):
    """$A231: ptr = word at $7B00 + (id-1)*2."""
    a = PTR_TABLE + ((sid - 1) & 0xFF) * 2
    return mem[a] | (mem[a + 1] << 8)


def decode(mem, ptr):
    """-> (attribute, 32 bitmap bytes) exactly as $A23E/$9DD2 consume them."""
    return mem[ptr], bytes(mem[ptr + 1:ptr + 1 + 32])


def slot_of(mem, dirbits):
    return mem[DIRTAB + (dirbits & 15)]


def sprite_id(mem, base_id, slot, phase):
    """$A4AF: id = (IX+15) + (IX+13) + 8*phase."""
    return (base_id + slot + 8 * phase) & 0xFF


def scr(base, x, y):
    return base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def unscr(addr, base=SHADOW):
    o = addr - base
    y = ((o >> 8) & 7) | ((o >> 2) & 0x38) | ((o >> 5) & 0xC0)
    return (o & 31) * 8, y


# --------------------------------------------------------------------------
# the live gate -- run the original and check the decode against its writes
# --------------------------------------------------------------------------
def gate(char, passes):
    """Drive the repaired original in every direction, trapping $9DD2.

    Returns (stats, records_seen) where stats counts, per direction:
      draws            player blits observed
      origin_ok        blit destination == (x-cam)*4 with NO offset
      pixels_ok        the 16x16 the blitter actually wrote == decode(record)
      attr_ok          the 2x2 attribute cells == record byte 0
      id_ok            the record the game chose == the id formula's prediction
    """
    stats = {}
    seen = {}
    for name in ('idle', 'up', 'down', 'left', 'right'):
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        fixchar.fix(h.memobj.m, char, 0)
        if name != 'idle':
            sel, bit = KM[DIRKEY[name]]
            h.ports.press(sel, keymask(bit))
        st = dict(draws=0, origin_ok=0, pixels_ok=0, attr_ok=0, id_ok=0)
        recs = []
        for _ in range(passes):
            _one_pass(h, st, recs)
        stats[name] = st
        seen[name] = recs
    return stats, seen


def gate2(char2, char1, passes):
    """The same gate for PLAYER 2, joined the way a second player really joins.

    $9440's only way in is BIT 4,(IX+7) -- so M is held for one pass and the
    original does the rest ($9689 places him, $96AB arms the six-frame
    materialise).  His blits come from $6320..$673F, ids 232.., and the id
    formula under test is the SAME one, with (IX+15) = $E8 instead of $D0.
    """
    stats, seen = {}, {}
    for name in ('idle', 'up', 'down', 'left', 'right'):
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        fixchar.fix(h.memobj.m, char1, char2)
        sel, bit = KM['M']                      # player 2's FIRE, measured
        h.ports.press(sel, keymask(bit))
        st = dict(draws=0, origin_ok=0, pixels_ok=0, attr_ok=0, id_ok=0)
        recs = []
        for _ in range(8):                      # join + the six materialise
            _one_pass(h, st, recs, P2, 0x6320, 0x6740)
        h.ports.release_all()
        if name != 'idle':
            sel, bit = KM[DIRKEY2[name]]
            h.ports.press(sel, keymask(bit))
        st = dict(draws=0, origin_ok=0, pixels_ok=0, attr_ok=0, id_ok=0)
        recs = []
        for _ in range(passes):
            _one_pass(h, st, recs, P2, 0x6320, 0x6740)
        stats[name] = st
        seen[name] = recs
    return stats, seen


def _one_pass(h, st, recs, blk=P1, bank_lo=0x5F00, bank_hi=0x6320):
    """Advance one main-loop pass (4 video frames), trapping the blitter."""
    target = h.regs[T] + 4 * FRAME_T
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    pending = None
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if pc == BLIT16:
            src = regs[SP]
            if bank_lo <= src < bank_hi:               # this player's bank
                pending = (regs[H] << 8 | regs[L], src, regs[C],
                           mem[blk], mem[blk + 1], mem[CAM_X], mem[CAM_Y],
                           mem[blk + 7], mem[blk + 13], mem[blk + 14],
                           mem[blk + 15], bank_lo)
        elif pc == BLIT16_END and pending is not None:
            _check(mem, pending, st, recs)
            pending = None
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def _check(mem, p, st, recs):
    dst, src, attr, px, py, cx, cy, f7, f13, f14, f15, bank = p
    ptr = src - 1
    st['draws'] += 1
    recs.append((ptr - bank) // REC)

    # 1. the draw origin, sampled AT the blit -- (x-cam)*4, no offset
    sx, sy = unscr(dst)
    if (sx, sy) == ((((px - cx) & 0x7E) >> 1) * 8, (((py - cy) & 0x7E) >> 1) * 8):
        st['origin_ok'] += 1

    # 2. the pixels the blitter actually wrote, read back out of the shadow
    #    screen with STANDARD Spectrum addressing (not the blitter's own
    #    arithmetic, so the read-back is not circular)
    want_attr, want_px = decode(mem, ptr)
    got = bytes(mem[scr(SHADOW, sx + b * 8, sy + r)]
                for r in range(16) for b in range(2))
    if got == want_px:
        st['pixels_ok'] += 1

    # 3. the 2x2 attribute block
    cells = [mem[0xD800 + ((sy // 8) + ry) * 32 + (sx // 8) + rx]
             for ry in range(2) for rx in range(2)]
    if all(c == want_attr == attr for c in cells):
        st['attr_ok'] += 1

    # 4. the id formula, $A47B..$A4B2, against the record the game chose
    phase = 0 if not (f14 & 0x40) else (2 if (f14 & 0x80) else 1)
    if record_ptr(mem, sprite_id(mem, f15, f13, phase)) == ptr:
        st['id_ok'] += 1


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
BANK1 = 0x5F00          # player 1's 32-record bank; player 2's is +$420


def extract(char, who=1, other=0):
    """-> (frames_by_name, base_id, mem).  Walks the game's own pointer table.

    `who` selects WHICH PLAYER'S bank is read.  The two banks are $5F00 (ids
    208..) and $6320 (ids 232..), the second being the first plus $420, and
    $9595 stamps eight pointers into $7C9E or $7CCE by `LD A,IXL / AND $20`.
    So player 2's records are found by walking the SAME pointer table from his
    own base id -- the table is the game's, not this tool's arithmetic.
    """
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if who == 1:
        mem = fixchar.fix(h.memobj.m, char, other)
    else:
        mem = fixchar.fix(h.memobj.m, other, char)
    blk = P1 if who == 1 else P2
    base_id = mem[blk + 15]
    out = {}
    for slot, name in enumerate(SLOTNAME):
        frames, attrs = [], []
        for phase in (0, 1, 2):
            ptr = record_ptr(mem, sprite_id(mem, base_id, slot, phase))
            a, bmp = decode(mem, ptr)
            frames.append(bmp)
            attrs.append(a)
        assert len(set(attrs)) == 1, f'{name}: attribute varies {attrs}'
        out[name] = (attrs[0], frames)
    # "idle" is not a separate pose: $A47B's `AND 15 / JR z` keeps the
    # PERSISTED (IX+13) when no direction is held.  Measured at level start
    # (IX+13) = 4 = slot S, so a standing player faces down.  Player 2's block
    # holds 0 until he joins, and $A4C0 ends his materialise with
    # LD (IX+13),4 -- the same slot, so both players idle facing SOUTH.
    idle_slot = mem[P1 + 13] if who == 1 else 4
    out['idle'] = out[SLOTNAME[idle_slot]]
    return out, base_id, mem


def sheet(out, path, scale=4):
    from PIL import ImageDraw
    pad, cw = 6, 16 * 4 + 6
    names = ['idle'] + SLOTNAME
    im = Image.new('RGB', (3 * cw + 90, len(names) * cw), (16, 16, 22))
    dr = ImageDraw.Draw(im)
    px = im.load()
    for r, name in enumerate(names):
        slot = '' if name == 'idle' else f'  slot {SLOTNAME.index(name)}'
        dr.text((6, r * cw + cw // 2 - 4), name + slot, fill=(190, 210, 255))
    for r, name in enumerate(names):
        attr, frames = out[name]
        ink, paper, br = attr & 7, (attr >> 3) & 7, (attr >> 6) & 1
        v = 255 if br else 205

        def rgb(c):
            return (v if c & 2 else 0, v if c & 4 else 0, v if c & 1 else 0)
        for k, bmp in enumerate(frames):
            ox, oy = 90 + k * cw, r * cw + pad // 2
            for row in range(16):
                for b in range(2):
                    bits = bmp[row * 2 + b]
                    for j in range(8):
                        col = rgb(ink if bits & (0x80 >> j) else paper)
                        for yy in range(scale):
                            for xx in range(scale):
                                px[ox + (b * 8 + j) * scale + xx,
                                   oy + row * scale + yy] = col
    im.save(path)
    return names


def main():
    args = sys.argv[1:]
    char, char2, passes, do_gate = 3, 1, 24, True
    while args:
        if args[0] == '--char':
            char = int(args[1]); del args[:2]
        elif args[0] == '--char2':
            char2 = int(args[1]); del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--no-gate':
            do_gate = False; del args[:1]
        else:
            del args[:1]

    out, base_id, mem = extract(char, who=1, other=char2)
    out2, base_id2, _ = extract(char2, who=2, other=char)
    print(f'character {char} ({fixchar.NAME[char]}), player-1 base id {base_id}, '
          f'attribute ${out["idle"][0]:02X}')
    print(f'character {char2} ({fixchar.NAME[char2]}), player-2 base id '
          f'{base_id2}, attribute ${out2["idle"][0]:02X}')

    if do_gate:
        for who, fn, arg in ((1, gate, char), (2, gate2, char2)):
            print(f'\nlive gate, PLAYER {who} -- {passes} passes per '
                  f'direction, trapping $9DD2:')
            stats, seen = fn(arg, passes) if who == 1 else fn(arg, char, passes)
            bad = 0
            for name, st in stats.items():
                n = st['draws']
                print(f'  {name:>6}: {n:3d} draws   origin {st["origin_ok"]}/{n}'
                      f'   pixels {st["pixels_ok"]}/{n}   '
                      f'attrs {st["attr_ok"]}/{n}   id {st["id_ok"]}/{n}   '
                      f'records {sorted(set(seen[name]))}')
                bad += 4 * n - (st['origin_ok'] + st['pixels_ok'] +
                                st['attr_ok'] + st['id_ok'])
                # every record the original drew must be one of the ones we emit
                for idx in set(seen[name]):
                    assert idx < 24, f'{name}: unexpected record {idx}'
            if bad:
                sys.exit(f'GATE FAILED (player {who}): {bad} mismatching checks')
            print('  gate: every check passed')

    doc = {'_geometry': {'w': 2, 'h': 16, 'px_w': 16, 'px_h': 16,
                         'bytes_per_record': REC, 'bitmap_at': 1,
                         'layout': 'row-major, 2 bytes per row, LEFT byte '
                                   'first, MSB first; opaque'},
           '_origin': {'dx': 0, 'dy': 0},
           '_char': char, '_base_id': base_id,
           '_slots': SLOTNAME,
           # $A5D8's 2-bit counter in bits 7:6 of (IX+14) decoded by
           # $A49F/$A4A7 -- the walk cycle, one entry per counter value
           '_phase_by_ctl': [0, 1, 0, 2],
           'w': 2, 'h': 16}
    for name in ['idle'] + SLOTNAME:
        attr, frames = out[name]
        assert (attr & 7) != 0, f'{name}: ink 0 would draw black on black'
        doc[name] = {'ink': attr, 'attr': attr,
                     'frames': [base64.b64encode(f).decode() for f in frames]}
    # PLAYER 2's set, under its own key.  The two banks are $5F00 and $6320
    # and the id bases $D0 and $E8 ($8420+15 / $8440+15, read out of live
    # RAM); everything else about the record is identical, which is why the
    # id formula gate above is run against BOTH players.
    p2 = {'_char': char2, '_base_id': base_id2}
    for name in ['idle'] + SLOTNAME:
        attr, frames = out2[name]
        assert (attr & 7) != 0, f'p2 {name}: ink 0 would draw black on black'
        p2[name] = {'ink': attr, 'attr': attr,
                    'frames': [base64.b64encode(f).decode() for f in frames]}
    doc['p2'] = p2

    # $F45E + n*$21 -- the SIX shared MATERIALISE records.  $9694 points a
    # joining player's first six sprite ids at them ($9579, stride $21) and
    # $A4C8 points them back six passes later; (IX+13) walks 0..5 meanwhile,
    # so record n is drawn on materialise pass n.  Attribute $06 on all six.
    mats = []
    for n in range(6):
        a, bmp = decode(mem, 0xF45E + n * REC)
        mats.append({'ink': a, 'frame': base64.b64encode(bmp).decode()})
    assert len({m['ink'] for m in mats}) == 1, 'materialise attribute varies'
    doc['materialise'] = mats

    # ALL FOUR CHARACTERS, and this is what the port was missing.
    #
    # Reported in play: "whatever character the player chooses the sprite is
    # always the elf".  The cause was NOT the sprite base -- (IX+$0F) is $D0
    # for player 1 and $E8 for player 2 for EVERY character, nothing in the
    # image ever writes it, and the port's `idx ? 232 : 208` is therefore
    # faithful.  MEASURED: booting from $8400 with ($FFFF) forced to 0..3 and
    # reading $842F/$844F gives $D0/$E8 four times over, while $8433/$8435 do
    # vary ($00/$8E, $08/$D8, $10/$32, $18/$64).
    #
    # The ARTWORK is swapped underneath that fixed base: hashing the eight
    # records at ids 208..215 across the four boots, ALL EIGHT DIFFER FOR ALL
    # FOUR characters.  So one extraction yields one character's sprites and
    # the port shipped exactly one -- whichever `--char` last ran.
    #
    # Emitted under 'chars' so the renderer can pick by the character index
    # the MENU chose, rather than by which extraction happened to run.
    doc['chars'] = []
    for k in range(4):
        outk, base_k, memk = extract(k, who=1, other=(k + 1) & 3)
        setk = {'_char': k, '_name': fixchar.NAME[k], '_base_id': base_k}
        for name in ['idle'] + SLOTNAME:
            attr, frames = outk[name]
            assert (attr & 7) != 0, f'char {k} {name}: ink 0 draws black on black'
            setk[name] = {'ink': attr, 'attr': attr,
                          'frames': [base64.b64encode(f).decode() for f in frames]}
        # $955B LD DE,$6218 / $9595 -- THE EXIT ANIMATION.  $6218 is
        # $5F00 + $318 = RECORD 24 of this character's own 32-record set, so
        # records 24..31 are the eight exit frames and $9595 restamps the
        # player's eight pointer-table entries ($7C9E, id $D0) to them when
        # (IX+$16) reaches exactly 7.  MEASURED on the real Z80: the table
        # holds 5F00..5FE7 for the first 17 passes of the exit and
        # 6218..62FF for the last 7 (`scratchpad/exitseq.py`).
        # Read straight out of the same repaired bank the walk frames come
        # from, at base + $21*k -- the stride $9587 itself uses.
        exitf, exita = [], []
        for rec in range(24, 32):        # `rec`, NOT `k`: k is the CHARACTER
            ptr = BANK1 + 0x21 * rec
            a, bmp = decode(memk, ptr)
            exitf.append(base64.b64encode(bmp).decode())
            exita.append(a)
        setk['exit'] = {'frames': exitf, 'inks': exita}
        doc['chars'].append(setk)
        print(f'  char {k} ({fixchar.NAME[k]}): base id {base_k}, '
              f'attribute ${outk["idle"][0]:02X}')
    # the four sets MUST differ, or the extraction silently produced one
    # character four times -- which is the very bug this block exists to fix
    sigs = {k: doc['chars'][k]['down']['frames'][0] for k in range(4)}
    assert len(set(sigs.values())) == 4, \
        f'the four character sets are not distinct: {sigs}'

    png = os.path.join(ROOT, 'build', 'player_frames.png')
    names = sheet(out, png)
    sheet(out2, os.path.join(ROOT, 'build', 'player2_frames.png'))
    path = os.path.join(ROOT, 'build', 'player_frames.json')
    json.dump(doc, open(path, 'w'), indent=1)
    print(f'\nrows in build/player_frames.png (3 phases each): '
          f'{", ".join(names)}')
    print(f'wrote {path}')


if __name__ == '__main__':
    main()
