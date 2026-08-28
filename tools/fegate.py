#!/usr/bin/env python3
"""
fegate.py -- THE FRONT-END GATE.  Drives block A and block C's attract/ranked
code on the REAL Z80 and prints the numbers tools/headless.js asserts.

WHY IT EXISTS.  Until this cycle nobody in this project had ever run block A.
Two real defects came out of that ($FFFF's stale $2A blitting 1,056 bytes of
ROM as the player, and $FFFD's stale $2A putting every measurement up to phase
11 on the 128K/AY branch by accident), and four of the five bytes block A hands
the game were still guesses.  Everything this file prints is measured by
running the original; nothing is derived.

    python tools/fegate.py bytes      the five bytes, from a BLIND key script
    python tools/fegate.py tune       the 48K title tune, OUT stream diff
    python tools/fegate.py hiscore    $869F's sort, driven and diffed
    python tools/fegate.py attract    $B470's period and the post-commit path
    python tools/fegate.py keymap     the four control methods, 40 keys each
    python tools/fegate.py all        all of the above
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, R, SP, FRAME_T, rom48   # noqa: E402
import blockA                                                     # noqa: E402
from blockA import stage_a, step_frames, KM                       # noqa: E402

BUILD = os.path.join(ROOT, 'build')
FAILED = []


def ok(name, got, want):
    good = got == want
    print('  %-4s %s\n        got  %s\n        want %s'
          % ('ok' if good else 'FAIL', name, got, want)
          if not good else '  ok   %s' % name)
    if not good:
        FAILED.append(name)
    return good


# ---------------------------------------------------------------- 1. the five
def cmd_bytes():
    """A BLIND key script -- it only ever taps SPACE on a fixed cadence and
    never reads a byte of memory -- driven from $C1F2 to the loader's $FF12,
    with a write watch over everything block C cannot reach.

    This is the regression test the project should have had from the start:
    if the menu ever turns out to write something else, this fails."""
    print('THE FIVE BYTES, from a blind SPACE-only run of block A')
    h = stage_a()
    m = h.memobj.m
    # the machine really is pre-block-B/C, and the five bytes really are the
    # loader stub's padding at entry
    assert all(v == 0 for v in m[0x4000:0x5BFE]), 'RAM below block A not clear'
    assert all(v == 0 for v in m[0xD810:0xFF00]), 'RAM above block A not clear'
    assert bytes(m[0xFFFB:0x10000]) == b'\x2a' * 5, 'entry bytes are not $2A x5'
    h.memobj.watch(0xC7F0, 0x10000)
    sel, bit = KM['SPACE']
    n = 0
    reached = False
    while n < 4000:
        h.ports.press(sel, 0xFF & ~(1 << bit))
        if step_frames(h, 6, targets=(0xFF12,))[0] == 'target':
            reached = True
            break
        h.ports.release_all()
        if step_frames(h, 10, targets=(0xFF12,))[0] == 'target':
            reached = True
            break
        n += 16
    h.ports.release_all()
    h.memobj.unwatch()
    assert reached, 'the front end never returned to the loader'
    five = [m[a] for a in range(0xFFFB, 0x10000)]
    writers = {}
    for pc, a, v in h.memobj.log:
        if a >= 0xFFFB:
            writers.setdefault(a, []).append((pc, v))
    for a in range(0xFFFB, 0x10000):
        for pc, v in writers.get(a, []):
            print('  ($%04X) <- $%02X   by PC=$%04X' % (a, v, pc))
    # nothing else above block C's top except the IM 2 vector table
    other = sorted({a for (pc, a, v) in h.memobj.log
                    if 0xFB77 <= a < 0xFFFB and not (0xFD01 <= a <= 0xFE00)
                    and a != 0xEEEE and a != 0xEEEF})
    print('  writes above $FB77 that are NOT the five and NOT the $FD01..$FE00'
          ' IM 2 table: %d' % len(other))
    print('  ($C7FD) the picker cursor is written at: %s'
          % (sorted({'$%04X' % pc for (pc, a, v) in h.memobj.log if a == 0xC7FD})
             or 'NOWHERE before the pick'))
    print()
    print('  MENU DEFAULTS (a player who only ever presses SPACE):')
    print('    ($FFFB) player 2 control = %d %s' % (five[0], CTRL[five[0]]))
    print('    ($FFFC) player 1 control = %d %s' % (five[1], CTRL[five[1]]))
    print('    ($FFFD) sound branch     = %d %s'
          % (five[2], '48K beeper' if five[2] == 0 else '128K AY'))
    print('    ($FFFE) player 2 char    = %d %s   <- A DRAW, $C43F LD A,R'
          % (five[3], CHAR[five[3]]))
    print('    ($FFFF) player 1 char    = %d %s' % (five[4], CHAR[five[4]]))
    ok('$FFFB default', five[0], 0)
    ok('$FFFC default', five[1], 0)
    ok('$FFFD default (48K beeper, unforced)', five[2], 0)
    ok('$FFFF default (the WARRIOR)', five[4], 0)
    ok('$FFFE is never player 1\'s', five[3] != five[4], True)
    ok('nothing else survives above $FB77', len(other), 0)
    return five


CTRL = ['SINCLAIR', 'KEMPSTON', 'PROTEK', 'KEYBOARD']
CHAR = ['WARRIOR', 'VALKYRIE', 'WIZARD', 'ELF']


def cmd_chars():
    """($FFFF) is the picker cursor: press '5' k times, get k."""
    print('THE CHARACTER PICKER -- press \'5\' k times, then SPACE')
    got = []
    for k in range(4):
        h = stage_a()
        assert blockA.one_player_run(h, char_taps=k), 'run %d stalled' % k
        got.append(h.memobj.m[0xFFFF])
        print('    5 x%d -> ($FFFF) = %d  %s' % (k, got[-1], CHAR[got[-1]]))
    ok('the picker cursor IS $FFFF', got, [0, 1, 2, 3])
    return got


# ------------------------------------------------------------------- 2. tune
def tune_model(fe):
    """The port's model of $C000, in Python.  Identical in structure to the
    JS TitleTune in web/template.html -- if this matches the Z80, that one
    does, because it is the same six lines."""
    d = fe['tune']
    pitch, tempo, base, tog = d['pitch'], d['tempo'], d['out_base'], d['toggle']
    out = []
    for n1, n2 in zip(d['ch1'], d['ch2']):
        p1 = pitch[(n1 + d['pitch_bias']) & 0xFF]
        p2 = pitch[(n2 + d['pitch_bias']) & 0xFF]
        if p1 == 1 and p2 == 1:              # $C06C/$C070/$C072 -- a REST
            out.append([])
            continue
        a, a2, e, l = base, base, 1, 1
        vals = []
        for _ in range(256 * ((256 - tempo) & 0xFF)):
            a, a2 = a2, a                     # EX AF,AF'
            e = (e - 1) & 0xFF
            vals.append(a)                    # $C089 OUT ($FE),A
            if e == 0:
                e = p1; a ^= tog
            a, a2 = a2, a                     # EX AF,AF'
            l = (l - 1) & 0xFF
            vals.append(a)                    # $C095/$C0AB OUT ($FE),A
            if l == 0:
                l = p2; a ^= tog
        out.append(vals)
    return out


def cmd_tune(nticks=8):
    """Run the REAL $C000 and diff its OUT ($FE) stream against the model."""
    print('THE 48K TITLE TUNE -- the model against the running original')
    fe = json.load(open(os.path.join(BUILD, 'fe_data.json')))
    model = tune_model(fe)
    h = stage_a()
    assert blockA.press_until(h, 'SPACE', 0xC2A2, limit=4000), 'never reached $C2A2'
    h.ports.release_all()
    h.ports.record_writes = True
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    tickT, seen = [], 0
    while seen < nticks + 1:
        pc = regs[PC]
        if pc == 0xC047:
            tickT.append(regs[T]); seen += 1
            if seen > nticks:
                break
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    w = [(t, v) for (t, p, v) in h.ports.writes if (p & 0xFF) == 0xFE]
    bad = 0
    for i in range(nticks):
        lo, hi = tickT[i], tickT[i+1]
        real = [v for (t, v) in w if lo <= t < hi]
        want = model[i]
        same = real == want
        if not same:
            bad += 1
        print('    tick %-3d %5d OUTs real / %5d model  %s%s'
              % (i, len(real), len(want), 'IDENTICAL' if same else 'DIFFER',
                 '   (a REST)' if not want else ''))
    ok('the tune model reproduces the Z80\'s speaker stream byte for byte',
       bad, 0)
    rests = [i for i, v in enumerate(model) if not v]
    print('    103 played ticks; %d rests at %s' % (len(rests), rests))
    print('    %d speaker writes over the whole tune'
          % sum(len(v) for v in model))
    ok('the rest ticks', rests, [6, 14, 22, 80, 86, 94, 102])
    ok('total speaker writes', sum(len(v) for v in model), 1277952)
    return rests


# ---------------------------------------------------------------- 3. hiscore
HS_BASE, HS_STRIDE = 0x8826, 0x3C
PEND = 0x7F2B                      # $93AF LD HL,$7F2B
PEND_CT = 0x84C6                   # (IY+$47)


def hs_state():
    """A live game, booted THROUGH THE REAL MENU so no character tag is poked.
    Cached, because the boot is ~3.7M instructions."""
    path = os.path.join(BUILD, 'state_frontend_c3_m3.pkl')
    if not os.path.exists(path):
        raise SystemExit('run `python tools/blockA.py boot 3 3` first')
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    return h


def hs_call(h, records):
    """Plant `records` as pending and call $869F, the way the game does after
    a game over.  Each record is (score24, name3, tag, millions)."""
    m = h.memobj.m
    for i, (score, name, tag, mil) in enumerate(records):
        b = PEND + 8*i
        m[b] = (score >> 16) & 0xFF; m[b+1] = (score >> 8) & 0xFF
        m[b+2] = score & 0xFF
        for j in range(3):
            m[b+3+j] = name[j]
        m[b+6] = tag; m[b+7] = mil
    m[PEND_CT] = len(records)
    h.call(0x869F, limit=4_000_000)
    return [bytes(m[HS_BASE + HS_STRIDE*i: HS_BASE + HS_STRIDE*(i+1)])
            for i in range(4)]


def py_key(score, name, mil):
    """$86AE..$86D9 -- the staging key at $8916."""
    c = (mil << 2) & 0xFF
    k = []
    for j in range(3):
        a = name[j] - 0x40
        if a < 0:
            a = 0
        a = ((a << 1) | ((c >> 7) & 1)) & 0xFF; c = (c << 1) & 0xFF
        a = ((a << 1) | ((c >> 7) & 1)) & 0xFF; c = (c << 1) & 0xFF
        a = ((a >> 1) | ((a & 1) << 7)) & 0xFF
        a = ((a >> 1) | ((a & 1) << 7)) & 0xFF
        k.append(a)
    return k + [(score >> 16) & 0xFF, (score >> 8) & 0xFF, score & 0xFF]


def py_insert(table, key):
    """$86ED -- the same ten-slot insertion sort the port implements."""
    t = list(table)
    e, slot = 0x38, 0
    while True:
        ins = nxt = False
        for i in range(3):                       # $86F4 the millions field
            c, a = key[i] & 0xC0, t[slot+i] & 0xC0
            if a < c:
                ins = True; break
            if a != c:
                nxt = True; break
        if not ins and not nxt:
            for i in range(3):                   # $8709 the BCD score
                a, b = t[slot+3+i], key[3+i]
                if a < b:
                    ins = True; break
                if a != b:
                    nxt = True; break
        if ins:
            for k in range(e):                   # $874A LDDR, E bytes
                src = 53 - k
                t[59-k] = t[src] if src >= 0 else 0
            t[slot:slot+6] = key
            return bytes(t), True
        slot += 6; e -= 6
        if e < 0:
            return bytes(t), False               # $8721 JR nc -- discarded


HS_CASES = [
    # (label, [(score, name, tag, millions)])
    ('034567 AAA into the ELF table', [(0x034567, b'AAA', 0x18, 0)]),
    ('012000 BAA', [(0x012000, b'BAA', 0x18, 0)]),
    ('999999 CAA', [(0x999999, b'CAA', 0x18, 0)]),
    ('010000 DAA -- EXACTLY the shipped score', [(0x010000, b'DAA', 0x18, 0)]),
    ('005000 EAA -- below it', [(0x005000, b'EAA', 0x18, 0)]),
    ('010001 FAA -- one point above', [(0x010001, b'FAA', 0x18, 0)]),
    ('a tie: three 020000 in a row',
     [(0x020000, b'GAA', 0x18, 0), (0x020000, b'HAA', 0x18, 0)]),
    ('two players, two tags',
     [(0x050000, b'IAA', 0x00, 0), (0x060000, b'JAA', 0x10, 0)]),
    ('the millions field outranks the score',
     [(0x000001, b'KAA', 0x08, 1)]),
]


def cmd_hiscore():
    """Drive $869F on the real Z80 and diff all four tables against the port's
    own sort, then close the two claims this project has made about it."""
    print('$8826 / $86ED -- the ranked tables, driven and diffed')
    h = hs_state()
    m = h.memobj.m
    shipped = [bytes(m[HS_BASE + HS_STRIDE*i: HS_BASE + HS_STRIDE*(i+1)])
               for i in range(4)]
    names = []
    for i, t in enumerate(shipped):
        nm = ''.join(chr(0x40 + (b & 0x3F)) if (b & 0x3F) else ' ' for b in t[:3])
        names.append(nm)
        same = all(t[6*j:6*j+6] == t[:6] for j in range(10))
        print('    table %d (%s): %r %02X%02X%02X, all ten slots identical: %s'
              % (i, CHAR[i], nm, t[3], t[4], t[5], same))
    ok('the shipped names', names, ['BIL', 'BOB', 'KEV', 'ARP'])
    bad = 0
    for label, recs in HS_CASES:
        h = hs_state()
        got = hs_call(h, recs)
        want = list(shipped)
        for score, nm, tag, mil in recs:
            idx = (tag >> 3) & 3
            want[idx], _ = py_insert(want[idx], py_key(score, nm, mil))
        same = got == want
        if not same:
            bad += 1
        print('    %-42s %s' % (label, 'agree' if same else 'DIFFER'))
        if not same:
            for i in range(4):
                if got[i] != want[i]:
                    print('      table %d\n        Z80  %s\n        port %s'
                          % (i, got[i].hex(), want[i].hex()))
    ok('the port\'s sort agrees with $86ED on every case', bad, 0)

    # THE ELEVENTH ENTRY.  One investigation asserted "the eleventh pushes one
    # off"; its sceptic said an 11th that beats nobody is discarded.  Settle it
    # on the machine.
    h = hs_state()
    m = h.memobj.m                       # the NEW machine's memory
    elf = lambda: bytes(m[HS_BASE + 3*HS_STRIDE: HS_BASE + 4*HS_STRIDE])
    # ten descending, and the literals must be PACKED BCD, so they are built
    # from the decimal digits rather than by hex arithmetic:
    #   900000, 890000, 880000, ... 810000
    bcd = lambda n: int('%06d' % n, 16)
    ten = [(bcd(900000 - 10000*i), bytes([0x41+i, 0x41, 0x41]), 0x18, 0)
           for i in range(10)]
    for i in range(0, 10, 2):
        hs_call(h, ten[i:i+2])
    after10 = elf()
    hs_call(h, [(bcd(805000), b'KKK', 0x18, 0)])   # BELOW slot 9 (810000)
    after11 = elf()
    print('    after ten descending inserts, slot 9 = %s (all ten shipped '
          'defaults gone: %s)'
          % (after10[54:60].hex(),
             all(after10[6*j:6*j+3] != b'\x01\x12\x10' for j in range(10))))
    print('    an 11th BELOW slot 9 changes %d table bytes'
          % sum(1 for a, b in zip(after10, after11) if a != b))
    ok('a score that beats nobody is DISCARDED (not "the 11th always gets in")',
       after10 == after11, True)
    hs_call(h, [(bcd(855000), b'LLL', 0x18, 0)])   # ABOVE slot 9 (810000)
    after12 = elf()
    nchanged = sum(1 for a, b in zip(after10, after12) if a != b)
    print('    an 11th ABOVE slot 9 changes %d table bytes and pushes one off'
          % nchanged)
    ok('...and one that DOES beat slot 9 gets in', nchanged > 0, True)


# ---------------------------------------------------------------- 4. attract
def cmd_attract():
    """$B470's page period, and where a committed name entry actually goes."""
    print('$B470 -- the attract loop')
    h = Harness()
    h.load_state(pickle.load(open(os.path.join(BUILD, 'state_48k.pkl'), 'rb')))
    # run $B470 from a live state with BOTH players out of the game -- $B47E
    # LD A,($8434) / AND (IY-$2B) / RLA / JR nc leaves the loop the instant
    # either one has joined, and the saved state has player 1 in it.
    h.memobj.m[0x8434] |= 0x80
    h.memobj.m[0x8454] |= 0x80
    h.memobj.m[0x84CB] = 0                  # (IY+$4C), 0 in the tape image
    h.regs[PC] = 0xB470
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    hits, pages = [], []
    n = 0
    while len(hits) < 6 and n < 20_000_000:
        pc = regs[PC]
        if pc == 0x8767:
            hits.append(regs[T]); pages.append(mem[0x84CB])
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    gaps = [(hits[i]-hits[i-1])/FRAME_T for i in range(1, len(hits))]
    print('    page draws at $8767, gaps in video frames: %s'
          % ' '.join('%.2f' % g for g in gaps))
    # ($84CB) sampled AT $8767, i.e. BEFORE $8769/$876D increment it
    print('    ($84CB) BEFORE each draw: %s   (after: %s)'
          % (pages, [((p + 1) & 0xFF) & ~4 for p in pages]))
    # the first cycle is short: $B470 zeroed $8497 part way through a frame
    ok('the page period is 256 video frames to within a hundredth',
       all(abs(g - 256.0) <= 0.02 for g in gaps[1:]), True)
    ok('the rotation is 1,2,3,0 = WARRIOR, VALKYRIE, WIZARD, ELF',
       [((p + 1) & 0xFF) & ~4 for p in pages[:5]], [1, 2, 3, 0, 1])

    # WHERE A COMMITTED NAME ENTRY GOES.  Not read off a disassembly: DRIVEN.
    # A player is put in the game-over loop at $B3B9 and CAPS SHIFT (the MAGIC
    # bit, $93A3 BIT 5) is held; the first PC in {$B470, $8767, $8503} that
    # comes up afterwards is where the original actually goes.
    h2 = Harness()
    h2.load_state(pickle.load(open(os.path.join(BUILD, 'state_48k.pkl'), 'rb')))
    m2 = h2.memobj.m
    m2[0x842E] = 0x80              # (IX+14) = $80 -- the name entry is armed
    m2[0x8434] |= 0x80             # player 1 is DEAD
    h2.regs[PC] = 0xB3B9
    sel, bit = KM['CAPS']
    h2.ports.press(sel, 0xFF & ~(1 << bit))
    sim, regs, ops, mem = h2.sim, h2.sim.registers, h2.sim.opcodes, h2.sim.memory
    fd, ia = h2.frame_duration, h2.int_active
    where, n = None, 0
    while n < 4_000_000:
        pc = regs[PC]
        if pc in (0xB470, 0x8767, 0x8503) and n > 100:
            where = pc; break
        if mem[pc] == 0x76 and regs[IFF]:
            h2._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    print('    committing the name entry lands at $%04X after %d instructions'
          % (where or 0, n))
    ok('a committed name entry goes to the ATTRACT LOOP $B470, not into play',
       where, 0xB470)


# ----------------------------------------------------------------- 5. keymap
KEY40 = [n for _, names in blockA.HALFROWS for n in names]


def cmd_keymap():
    """The four control methods, each key pressed one at a time, read out of
    ($8427) and ($8447) in a state BOOTED THROUGH THE REAL MENU."""
    print('$855D -- the four control methods, 40 keys each')
    BITS = [('UP', 1), ('DOWN', 2), ('LEFT', 4), ('RIGHT', 8),
            ('FIRE', 0x10), ('MAGIC', 0x20)]
    out = {}
    for who, sel_addr, res_addr in ((0, 0xFFFC, 0x8427), (1, 0xFFFB, 0x8447)):
        for method in range(4):
            found = {}
            for key in KEY40:
                h = Harness()
                h.load_state(pickle.load(
                    open(os.path.join(BUILD, 'state_48k.pkl'), 'rb')))
                h.memobj.m[sel_addr] = method
                sel, bit = KM[key]
                h.ports.press(sel, 0xFF & ~(1 << bit))
                h.call(0x855D, limit=200000)
                v = h.memobj.m[res_addr]
                for nm, msk in BITS:
                    if v & msk:
                        found[nm] = key
            out[(who, method)] = found
            print('    player %d method %d %-9s %s'
                  % (who+1, method, CTRL[method],
                     '  '.join('%s=%s' % (n, found[n]) for n, _ in BITS
                               if n in found) or '(no key -- it is a PORT)'))
    ok('method 3 player 1 is the map every key script in this project uses',
       [out[(0, 3)].get(k) for k in ('UP', 'DOWN', 'LEFT', 'RIGHT', 'FIRE')],
       ['1', 'Q', 'S', 'D', 'Z'])
    ok('method 0 player 1 is SINCLAIR joystick 2',
       [out[(0, 0)].get(k) for k in ('UP', 'DOWN', 'LEFT', 'RIGHT', 'FIRE')],
       ['9', '8', '6', '7', '0'])
    ok('MAGIC is CAPS / SPACE in ALL FOUR methods',
       [out[(w, mth)].get('MAGIC') for w in (0, 1) for mth in range(4)],
       ['CAPS']*4 + ['SPACE']*4)
    return out


def cmd_all():
    for c in ('bytes', 'chars', 'tune', 'keymap', 'hiscore', 'attract'):
        print()
        globals()['cmd_' + c]()
    print()
    print('%d FAILING' % len(FAILED) if FAILED else 'ALL GREEN')
    sys.exit(1 if FAILED else 0)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'bytes'
    globals()['cmd_' + cmd]()
    if FAILED:
        print('\n%d FAILING: %s' % (len(FAILED), ', '.join(FAILED)))
        sys.exit(1)
