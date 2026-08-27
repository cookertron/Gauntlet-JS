#!/usr/bin/env python3
"""
p2gate.py -- the TWO-PLAYER differential, and the measurements behind it.

    python tools/p2gate.py diff              every scenario, both sides
    python tools/p2gate.py table join 20     the ORIGINAL's table
    python tools/p2gate.py join              the join, byte by byte
    python tools/p2gate.py leash             $A924 / $A944 enumerated
    python tools/p2gate.py overlap           $AAC4 enumerated
    python tools/p2gate.py camera            $A3E6 against a transcription
    python tools/p2gate.py place             $9689's ring, walled one at a time
    python tools/p2gate.py ff                the three friendly-fire modes
    python tools/p2gate.py chase             $ADC7's five arms
    python tools/p2gate.py all               everything above

The differential runs the SAME SCRIPT on the real Z80 and on the built engine
and compares the printed table row for row, exactly as tools/pointdiff.py does
for one player:

    python tools/p2gate.py table <scenario>            the real Z80
    node   tools/headless.js --p2table <script>        the built artifact

=============================================================================
THE SAMPLER IS $8503, AS IT MUST BE
=============================================================================
Every row of every table here is taken at the main-loop top.  A pass is
3.92..5.03 video frames and a four-frame window is not a sampler; this file
steps until PC == $8503 and reads the two player blocks there.  (The same
lesson tools/sim_move.py's docstring records at length.)

=============================================================================
THE SCRIPT LANGUAGE
=============================================================================
A script is a comma-separated list of `count:p1:p2` segments, where each key
field is a subset of the letters

    U D L R   the four directions        F  fire        S  shift/potion

or `-` for nothing held.  So

    1:-:F,8:-:-,12:R:L

is one pass with only player 2's FIRE held (the join), eight passes of
nothing (his six-frame materialise plus slack), then twelve with player 1
walking right and player 2 walking left.

On the real Z80 the letters are pressed as KEYS, through the same $B4E8 scan
the game uses, and both players fall through $8560/$8589's four-way dispatch
to the keyboard arms $862E and $8657 because $FFFC/$FFFB hold the stub's own
$2A.  Measured key map:

    player 1   1 up  Q down  S left  D right  Z fire  CAPS shift
    player 2   8 up  I down  K left  L right  M fire  SPACE shift

=============================================================================
WHAT IS POKED, AND WHY
=============================================================================
`--noactors` (the default for the scripted scenarios) zeroes $8496 and the
tail pointer $8494 on the Z80 side and empties the actor list on the engine
side.  That is not hiding a disagreement: the actor update draws twice from
`LD A,R` on every pass ($AC25, $AC4C), which no port can reproduce, and
NOTES-engine.md already measures the resulting drift (every actor agrees for
12 passes, then diverges).  The two-player rules under test -- the join, the
leash, the overlap box, the order swap, the shove, the friendly-fire modes,
the camera midpoint, the two HUD round robins and the two drains -- involve
no entropy at all, so with the actors off both sides are exactly comparable
for as long as you like.  The `live` scenario leaves them ON for 12 passes,
which is inside the measured agreement window.
"""
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, F, SP, TAPE_CALL_PC      # noqa: E402
from keyprobe import KEYS, keymask                                # noqa: E402

import re
ROW = re.compile(r'^\s*\d+\s+\S.*\|.*\|')
KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')   # the 48K baseline
LOOP_TOP = 0x8503
P1, P2 = 0x8420, 0x8440

# $862E (player 1) and $8657 (player 2), the "else" arms of the two dispatches
KEYMAP = {
    0: {'U': '1', 'D': 'Q', 'L': 'S', 'R': 'D', 'F': 'Z', 'S': 'CAPS'},
    1: {'U': '8', 'D': 'I', 'L': 'K', 'R': 'L', 'F': 'M', 'S': 'SPACE'},
}


# --------------------------------------------------------------------------
def fresh():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def step_to_top(h, limit=8_000_000):
    """Run until PC reaches $8503 again -- ONE main-loop pass, by construction.

    Handles the two cases the harness's own stepper does: the tape breakpoint
    and a HALT with interrupts enabled.
    """
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < limit:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


_SP0 = {}


def call(h, addr, regs=None):
    """h.call with SP restored.  h.call pushes six sentinels and a clean RET
    pops one, so SP walks down ten bytes per call and eventually tramples
    RAM -- which manufactures 'mismatches' that are the tool's own fault."""
    if id(h) not in _SP0:
        _SP0[id(h)] = h.sim.registers[SP]
    h.sim.registers[SP] = _SP0[id(h)]
    return h.call(addr, regs)


def press(h, k1, k2):
    h.ports.release_all()
    for who, keys in ((0, k1), (1, k2)):
        for ch in keys:
            if ch == '-':
                continue
            sel, bit = KM[KEYMAP[who][ch]]
            h.ports.press(sel, keymask(bit))


def parse(script):
    """'1:-:F,8:-:-' -> [(1,'-','F'), (8,'-','-')]"""
    out = []
    for seg in script.split(','):
        n, a, b = seg.split(':')
        out.append((int(n), a, b))
    return out


def kill_actors(h):
    m = h.memobj.m
    m[0x8496] = 0                       # $8496, the live count
    m[0x8494], m[0x8495] = 0x00, 0x5C   # $8494, the tail -> $5C00


# --------------------------------------------------------------------------
# THE SCENARIOS.  Each is (script, options) and both sides run the same pair.
# --------------------------------------------------------------------------
SCEN = {
    # the join itself, the six materialise passes, then both walking apart
    'join':   ('1:-:F,8:-:-,14:L:R', {}),
    # straight into each other: $AAC4 refuses, $A39B swaps the order,
    # $AB15 shoves, and the pair advances at half speed
    'push':   ('1:-:F,8:-:-,20:R:-', {}),
    # head-on, which DEADLOCKS: a player holding a direction cannot be shoved
    'headon': ('1:-:F,8:-:-,20:R:L', {}),
    # the vertical leash: player 1 walks down until $A944 refuses at 36
    'leash':  ('1:-:F,8:-:-,30:D:-', {}),
    # both walking onto ONE key, four units apart on each axis
    'item':   ('1:-:F,8:-:-,16:R:D', {'plant': (4, 10, 0x1F),
                                      'warp': (12, 40, 16, 36)}),
    # player 1 fires east into player 2 -- with the dungeon's own flags this
    # is the "neither" mode, so the partner absorbs the shot
    # (the two 'R' passes are there to TURN him: holding fire freezes the
    # player where he stands but $A47B still tracks the held direction, and a
    # shot flies the way he faces -- so without them he fires SOUTH)
    'shoot':  ('1:-:F,8:-:-,2:R:-,30:F:-', {'warp': (12, 40, 40, 40)}),
    # the same with $847E forced, one mode each
    'stun':   ('1:-:F,8:-:-,2:R:-,30:F:-', {'warp': (12, 40, 40, 40),
                                            'ff': 0x10}),
    'hurt':   ('1:-:F,8:-:-,2:R:-,30:F:-', {'warp': (12, 40, 40, 40),
                                            'ff': 0x20}),
    # both alive with the ACTORS RUNNING, inside the measured 12-pass window
    'live':   ('1:-:F,8:-:-,12:D:R', {'actors': True}),
}


def align(h):
    """Step to the first $8503 and return the scalars the engine must start
    from.  THE CAPTURE IS NOT AT A LOOP TOP -- its PC is $ABA1, inside the
    actor loop at $851E -- so this first step completes the REST of that pass,
    and the drain, the HUD round robin and the death check all run in it.  The
    engine cannot resume a half-finished pass, so the differential takes its
    initial condition from the ORIGINAL here and hands it to the engine: the
    two sides then start pass 1 in the same state, which is what a
    differential needs and what the one-player tables get for free (they
    print position, which that partial pass does not touch).
    """
    step_to_top(h)
    m = h.memobj.m
    return {'hp': (m[0x8422] << 8) | m[0x8423], 'frame': m[0x8497],
            'phase': m[0x849F], 'ctr': m[0x8491], 'hurry': m[0x84B8],
            'f11': m[0x842B]}


def step_pass(h, limit=8_000_000):
    """One pass, returning the VIDEO FRAME COUNTER as $B6DA saw it.

    The drain is the only rule in the game clocked by $8497 rather than by
    passes, and this engine has no T-state clock: it charges every pass
    exactly four frames where the original's cost 3.92..5.03 (and the six
    materialise passes are among the cheap ones).  Over the sixteen passes
    between two drain ticks that is enough to move the tick by one pass, so
    the differential MEASURES the original's clock and hands it to the
    engine -- the same substitution the port already makes for `LD A,R`, and
    for the same reason: what is under test is the rule, not the clock.
    Without it every scenario long enough to contain a tick reports two
    rows of skew that are nothing to do with two players.
    """
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    seen = None
    while n < limit:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return seen
        if pc == 0xB6DA:
            seen = mem[0x8497]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


def run_original(name, extra=0):
    script, opt = SCEN[name]
    h = fresh()
    seed = align(h)                         # align before sampling
    m = h.memobj.m
    if not opt.get('actors'):
        kill_actors(h)
    if 'plant' in opt:
        c, r, v = opt['plant']
        m[0x8000 + r * 32 + c] = v
    rows = []
    clock = []
    warped = False
    for count, k1, k2 in parse(script) + ([(extra, '-', '-')] if extra else []):
        for _ in range(count):
            press(h, k1, k2)
            clock.append(step_pass(h))
            if not warped and 'warp' in opt and m[P2 + 0x14] == 0 \
                    and m[P2 + 0x0E] == 0:
                # after the join AND the materialise, put the pair where the
                # scenario wants them; the engine does the same, and both
                # sides then recompute everything from those coordinates
                x1, y1, x2, y2 = opt['warp']
                m[P1], m[P1 + 1] = x1, y1
                m[P2], m[P2 + 1] = x2, y2
                if 'ff' in opt:
                    m[0x847E] = (m[0x847E] & ~0x30) | opt['ff']
                warped = True
            rows.append(sample(m))
    seed['clock'] = clock
    return rows, seed


def sample(m):
    def blk(b):
        return (m[b], m[b + 1], (m[b + 2] << 8) | m[b + 3],
                (m[b + 4] << 16) | (m[b + 5] << 8) | m[b + 6],
                m[b + 8], m[b + 9], m[b + 0x0B], m[b + 0x14], m[b + 0x1D],
                m[b + 0x12])
    return blk(P1) + blk(P2) + (m[0x848B], m[0x848C], m[0x848D], m[0x848E])


HDR = ('pass  p1x p1y  p1hp p1sc  k p f11 p14 pnd sht |'
       '  p2x p2y  p2hp p2sc  k p f11 p14 pnd sht | cam   tgt')


def fmt(i, r):
    return (f'{i:>4}  {r[0]:>3} {r[1]:>3}  {r[2]:04X} {r[3]:06X} '
            f'{r[4]:>2}{r[5]:>2} {r[6]:02X}  {r[7]:02X}  {r[8]:02X} {r[9]:02X} |'
            f'  {r[10]:>3} {r[11]:>3}  {r[12]:04X} {r[13]:06X} '
            f'{r[14]:>2}{r[15]:>2} {r[16]:02X}  {r[17]:02X}  {r[18]:02X} '
            f'{r[19]:02X} | {r[20]:>2},{r[21]:>2} {r[22]:>2},{r[23]:>2}')


def cmd_table(name='join', extra='0'):
    rows, seed = run_original(name, int(extra))
    print(HDR)
    for i, r in enumerate(rows):
        print(fmt(i + 1, r))


# --------------------------------------------------------------------------
def engine_rows(name, seed, extra=0, helped=True):
    script, opt = SCEN[name]
    args = ['node', os.path.join('tools', 'headless.js'), '--p2table', script]
    if not opt.get('actors'):
        args.append('--noactors')
    if 'plant' in opt:
        args += ['--plant', ','.join(str(v) for v in opt['plant'])]
    if 'warp' in opt:
        args += ['--warp', ','.join(str(v) for v in opt['warp'])]
    if 'ff' in opt:
        args += ['--ff', str(opt['ff'])]
    if extra:
        args += ['--extra', str(extra)]
    args += ['--seed', ','.join(str(seed[k]) for k in
                                ('hp', 'frame', 'phase', 'ctr', 'hurry',
                                 'f11'))]
    if helped:
        args += ['--clock', ','.join(str(v) for v in seed['clock'])]
    out = subprocess.run(args, capture_output=True, text=True, cwd=ROOT).stdout
    rows = []
    for line in out.splitlines():
        # a table row starts with the pass number and carries two bars; the
        # check list headless.js prints first does not match
        if not ROW.match(line):
            continue
        rows.append(line.rstrip())
    return rows


def cmd_diff(*names):
    names = names or tuple(SCEN)
    total = 0
    unhelped = 0
    for name in names:
        rows, seed = run_original(name)
        want = [fmt(i + 1, r) for i, r in enumerate(rows)]
        got = engine_rows(name, seed)
        if len(want) != len(got):
            print(f'{name:>7}: WRONG ROW COUNT orig={len(want)} engine={len(got)}')
            total += 1
            continue
        bad = [(a, b) for a, b in zip(want, got) if a != b]
        # AND THE UNHELPED RUN, so the size of the substitution stays visible
        raw = engine_rows(name, seed, helped=False)
        braw = (len([1 for a, b in zip(want, raw) if a != b])
                if len(raw) == len(want) else len(want))
        unhelped += braw
        print(f'{name:>7}: {len(want):>3} rows -> {len(bad)} mismatching'
              f'   ({braw} without the $8497 substitution)')
        for a, b in bad[:4]:
            print(f'    orig   {a}')
            print(f'    engine {b}')
        total += len(bad)
    print(f'\nTOTAL MISMATCHING TWO-PLAYER ROWS: {total}')
    print(f'  without the $8497 substitution: {unhelped}.  THE SUBSTITUTION IS')
    print('  STILL NEEDED, and this is why -- measured, not assumed.  $8497')
    print('  counts INTERRUPT HANDLER invocations, and an interrupt whose 32-T')
    print('  window lands inside one of the blitter\'s DI spans ($9F86, $A00D,')
    print('  $A0C8, $A241, $A292 -- they blit with LD SP,source and must) is')
    print('  LOST FOR EVER.  In these scenarios every pass costs four video')
    print('  frames and the original still advances $8497 by only 3.44..4.10 a')
    print('  pass, i.e. about 45% of the single W1 boundary\'s interrupts are')
    print('  lost; the engine charges one handler per boundary and so gives a')
    print('  flat 4.  Over the sixteen passes between two drain ticks that')
    print('  moves the tick by up to three passes, and every unhelped row')
    print('  above is exactly that: $842B bit 0 set a pass early or late, with')
    print('  the SAME health either side.  Closing it needs a cycle-exact')
    print('  model of the blitter\'s inner loop -- emulating the CPU rather')
    print('  than porting the game.  The pass COST model does not need it:')
    print('  `python tools/clockgate.py diff` is 280/280 exact on the scenes')
    print('  where the two simulations provably agree.')
    return total


# --------------------------------------------------------------------------
# the measurements -- every number a headless check asserts is printed here
# --------------------------------------------------------------------------
def cmd_join():
    """$9440 in play: hold M for one pass from the captured state."""
    h = fresh()
    step_to_top(h)
    m = h.memobj.m
    before = bytes(m[P2:P2 + 32])
    press(h, '-', 'F')
    step_to_top(h)
    press(h, '-', '-')
    after = bytes(m[P2:P2 + 32])
    print('THE JOIN, one pass with M held, player 1 at '
          f'({m[P1]},{m[P1 + 1]}):')
    for i in range(32):
        if before[i] != after[i]:
            print(f'  +${i:02X}  ${before[i]:02X} -> ${after[i]:02X}')
    print(f'  position ({after[0]},{after[1]})  health '
          f'${after[2]:02X}{after[3]:02X}')
    print('\nthe materialise, $7CCE (sprite id 232) per pass:')
    for i in range(8):
        ptr = m[0x7CCE] | (m[0x7CCF] << 8)
        print(f'  +{i}  $7CCE=${ptr:04X}  (IX+13)=${m[P2 + 13]:02X}  '
              f'(IX+14)=${m[P2 + 14]:02X}  pos ({m[P2]},{m[P2 + 1]})')
        step_to_top(h)


def runs(v):
    out = []
    for x in v:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return ','.join(f'{a}..{b}' if a != b else str(a) for a, b in out)


def cmd_leash():
    """$A924 / $A944, all 128 candidates, both IX values, partner at 0."""
    h = fresh()
    m = h.memobj.m
    print('THE LEASH, enumerated on the real routine:')
    for name, addr, other, ix in (('$A924 x, IX=$8420', 0xA924, P2, P1),
                                  ('$A924 x, IX=$8440', 0xA924, P1, P2),
                                  ('$A944 y, IX=$8420', 0xA944, P2, P1),
                                  ('$A944 y, IX=$8440', 0xA944, P1, P2)):
        m[P1 + 0x0B] &= 0x7F
        m[P2 + 0x0B] &= 0x7F
        allowed, refused = [], []
        for cand in range(128):
            m[other] = m[other + 1] = 0
            call(h, addr, {'IX': ix, 'BC': (cand << 8) | cand})
            (refused if h.sim.registers[F] & 1 else allowed).append(cand)
        print(f'  {name}: allowed {runs(allowed)}   REFUSED {runs(refused)}')
    m[P2 + 0x0B] |= 0x80
    for entry in (0, 1):
        outs = set()
        for cand in range(128):
            call(h, 0xA924, {'IX': P1, 'BC': (cand << 8) | cand, 'F': entry})
            outs.add(h.sim.registers[F] & 1)
        print(f'  partner absent, entry carry {entry} -> exit carry '
              f'{sorted(outs)}')


def cmd_overlap():
    """$AAC4 over the whole offset neighbourhood, both IX values."""
    h = fresh()
    m = h.memobj.m
    print('$AAC4, the other-player box:')
    for ix, other in ((P1, P2), (P2, P1)):
        m[P1 + 0x0B] &= 0x3F
        m[P2 + 0x0B] &= 0x3F
        m[other], m[other + 1] = 40, 40
        hits = []
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                m[ix + 14] = 0
                call(h, 0xAAC4, {'IX': ix,
                                 'BC': (((40 + dy) & 0x7F) << 8) | ((40 + dx) & 0x7F)})
                if h.sim.registers[F] & 1 or (m[ix + 14] & 8):
                    hits.append((dx, dy, h.sim.registers[F] & 1,
                                 (m[ix + 14] >> 3) & 1))
        dxs = sorted({a for a, b, c, d in hits})
        dys = sorted({b for a, b, c, d in hits})
        print(f'  IX=${ix:04X}: {len(hits)} hits, dx {dxs[0]}..{dxs[-1]}, '
              f'dy {dys[0]}..{dys[-1]}, full product '
              f'{len(hits) == len(dxs) * len(dys)}, carry == bit 3 always '
              f'{all(c and d for a, b, c, d in hits)}')
    m[P2 + 0x0B] |= 0x40
    m[P2], m[P2 + 1] = 40, 40
    for entry in (0, 1):
        m[P1 + 14] = 0
        call(h, 0xAAC4, {'IX': P1, 'BC': (40 << 8) | 40, 'F': entry})
        print(f'  other +$0B bit 6 set, entry carry {entry} -> exit carry '
              f'{h.sim.registers[F] & 1}, bit 3 {(m[P1 + 14] >> 3) & 1}')


def model_target(p1, p2, in1, in2):
    """An INDEPENDENT transcription of $A3E6, written from the disassembly."""
    if in1:
        x, y = p1
    elif in2:
        x, y = p2
    else:
        return None
    L = (x + 2) & 0xFF
    H = (y + 2) & 0xFF
    Cc, Aa = H, L
    if in1 and in2:
        e = (p2[0] + 2) & 0xFF
        d = (p2[1] + 2) & 0xFF
        s = ((H << 8) | L) + ((d << 8) | e)     # $A40B ADD HL,DE -- 16-bit
        H = ((s >> 8) & 0xFF) >> 1              # $A40C SRL H
        L = (s & 0xFF) >> 1                     # $A40E SRL L
    L &= 0xFE
    H &= 0xFE                                   # $A410 RES 0
    a = (Aa - L) & 0xFF
    if a & 0x80:
        a = (-a) & 0xFF
    if (a & 0x7F) >= 0x21:
        L ^= 0x40                               # $A41B the wrap fix
    L &= 0x7F
    a = (Cc - H) & 0xFF
    if a & 0x80:
        a = (-a) & 0xFF
    if (a & 0x7F) >= 0x21:
        H ^= 0x40
    H &= 0x7F
    return (L, H)


def cmd_camera():
    """$A3E6 against the transcription above, enumerated."""
    h = fresh()
    m = h.memobj.m
    pairs = [((x1, 20), (x2, 20)) for x1 in range(0, 128, 2)
             for x2 in range(0, 128, 2)]
    pairs += [((30, y1), (30, y2)) for y1 in range(0, 128, 2)
              for y2 in range(0, 128, 2)]
    bad = 0
    for p1, p2 in pairs:
        m[P1], m[P1 + 1] = p1
        m[P2], m[P2 + 1] = p2
        m[P1 + 0x0B] &= 0x7F
        m[P2 + 0x0B] &= 0x7F
        m[0x848D] = m[0x848E] = 0xEE
        call(h, 0xA3E6)
        if (m[0x848D], m[0x848E]) != model_target(p1, p2, True, True):
            bad += 1
    print(f'$A3E6 vs the transcription, both present: {len(pairs) - bad}/'
          f'{len(pairs)} agree')
    n = bad = 0
    for x1 in range(0, 128, 2):
        for y1 in (0, 8, 30, 62, 100, 126):
            m[P1], m[P1 + 1] = x1, y1
            m[P2 + 0x0B] |= 0x80
            m[P1 + 0x0B] &= 0x7F
            m[0x848D] = m[0x848E] = 0xEE
            call(h, 0xA3E6)
            n += 1
            if (m[0x848D], m[0x848E]) != model_target((x1, y1), (0, 0),
                                                      True, False):
                bad += 1
    print(f'  player 2 absent: {n - bad}/{n} agree')
    m[P1 + 0x0B] |= 0x80
    m[P2 + 0x0B] &= 0x7F
    m[P2], m[P2 + 1] = 40, 60
    m[0x848D] = m[0x848E] = 0xEE
    call(h, 0xA3E6)
    print(f'  player 1 absent, player 2 at (40,60) -> target '
          f'({m[0x848D]},{m[0x848E]})')
    m[P2 + 0x0B] |= 0x80
    m[0x848D] = m[0x848E] = 0xEE
    call(h, 0xA3E6)
    print(f'  both absent -> ({m[0x848D]},{m[0x848E]}) '
          f'(238,238 = not written at all)')


def cmd_place():
    """$9689's ring: wall the four cardinals one at a time and watch."""
    print('$9689, where the joining player lands (player 1 warped first):')
    for tag, walls in (('nothing walled', ()),
                       ('E walled', ('E',)),
                       ('E,N walled', ('E', 'N')),
                       ('E,N,S walled', ('E', 'N', 'S')),
                       ('all four walled', ('E', 'N', 'S', 'W'))):
        h = fresh()
        step_to_top(h)
        m = h.memobj.m
        kill_actors(h)
        m[P1], m[P1 + 1] = 40, 40
        col, row = 10, 10
        d = {'E': (1, 0), 'N': (0, -1), 'S': (0, 1), 'W': (-1, 0)}
        for w in walls:
            dc, dr = d[w]
            m[0x8000 + (row + dr) * 32 + (col + dc)] = 0x01
        press(h, '-', 'F')
        step_to_top(h)
        press(h, '-', '-')
        print(f'  {tag:>16}: player 2 -> ({m[P2]},{m[P2 + 1]})  '
              f'+$14 ${m[P2 + 0x14]:02X}  health ${m[P2+2]:02X}{m[P2+3]:02X}')


def cmd_ff():
    """The three friendly-fire modes, driven: p1 fires east into p2."""
    print('$847E bits 4/5 -- p1 firing east at p2 four cells away:')
    for tag, ff in (('neither', 0x00), ('bit 4 STUN', 0x10),
                    ('bit 5 HURT', 0x20), ('both', 0x30)):
        h = fresh()
        step_to_top(h)
        m = h.memobj.m
        kill_actors(h)
        press(h, '-', 'F')
        step_to_top(h)
        press(h, '-', '-')
        for _ in range(8):
            step_to_top(h)
        m[P1], m[P1 + 1] = 12, 40
        m[P2], m[P2 + 1] = 40, 40
        for _ in range(20):             # let the camera settle
            step_to_top(h)
        # TURN HIM EAST first.  Holding fire freezes the player where he
        # stands ($A57E) but $A47B still tracks the held direction and
        # $8CA0 builds the shot's state from (IX+13), so a player who has
        # never faced east fires SOUTH and the shot never reaches anyone.
        press(h, 'R', '-')
        step_to_top(h)
        step_to_top(h)
        m[0x847E] = (m[0x847E] & ~0x30) | ff
        m[P2 + 2], m[P2 + 3] = 0x20, 0x00
        press(h, 'F', '-')
        hp, p14, pend = [], set(), set()
        for _ in range(30):
            step_to_top(h)
            m[0x847E] = (m[0x847E] & ~0x30) | ff
            hp.append((m[P2 + 2] << 8) | m[P2 + 3])
            p14.add(m[P2 + 0x14])
            pend.add(m[P2 + 0x1D])
        print(f'  {tag:>11}: p2 health {hp[0]:04X} -> {hp[-1]:04X}  '
              f'+$14 {sorted(hex(v) for v in p14)}  '
              f'+$1D max ${max(pend):02X}')


def cmd_chase():
    """$ADC7's five arms, driven in isolation with the four bytes poked."""
    h = fresh()
    m = h.memobj.m
    print('$ADC7 -> the JR displacement at $AD52 '
          '($24 p1, $19 p2, $2F nearer, $00 random):')
    cases = [
        ('player 2 not in the game', {0x8454: 0x80, 0x842A: 0, 0x844A: 0}),
        ('  ... and p1 holds the $18', {0x8454: 0x80, 0x842A: 5, 0x844A: 0}),
        ('both in, neither holds it', {0x8454: 0x00, 0x842A: 0, 0x844A: 0,
                                       0x842B: 0, 0x844B: 0}),
        ('both in, p1 holds it', {0x8454: 0x00, 0x842A: 5, 0x844A: 0,
                                  0x842B: 0, 0x844B: 0}),
        ('both in, p2 holds it', {0x8454: 0x00, 0x842A: 0, 0x844A: 5,
                                  0x842B: 0, 0x844B: 0}),
        ('both hold it', {0x8454: 0x00, 0x842A: 5, 0x844A: 5,
                          0x842B: 0, 0x844B: 0}),
        ('p1 out of play', {0x8454: 0x00, 0x842A: 0, 0x844A: 0,
                            0x842B: 0x80, 0x844B: 0}),
        ('p2 out of play', {0x8454: 0x00, 0x842A: 0, 0x844A: 0,
                            0x842B: 0, 0x844B: 0x80}),
    ]
    for tag, pokes in cases:
        for a, v in pokes.items():
            m[a] = v
        call(h, 0xADC7)
        print(f'  {tag:<28} -> ${h.sim.registers[0]:02X}')


def cmd_all():
    for fn in (cmd_join, cmd_place, cmd_leash, cmd_overlap, cmd_camera,
               cmd_chase, cmd_ff):
        print()
        fn()
    print()
    return cmd_diff()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'diff'
    fn = globals().get('cmd_' + cmd)
    if fn is None:
        sys.exit(__doc__)
    rc = fn(*sys.argv[2:])
    sys.exit(1 if rc else 0)


if __name__ == '__main__':
    main()
