#!/usr/bin/env python3
"""
menugate.py -- DRIVE BLOCK A'S FRONT END and measure what the menus write.

Block A is the transient front end: the loader puts it at $8600, calls $C1F2
once, and block C then lands on top of it ($8400..$FB76 covers $8600..$D80F).
The ONLY thing it can hand the game is the five bytes above $FB76, and this
tool watches every write to $FB77..$FFFF for a whole run to prove there are no
others:

    $FFFB   player 2's control method  0..3   ($C51D, from $C808)
    $FFFC   player 1's control method  0..3   ($C508, from $C808)
    $FFFD   the SOUND branch  0 = 48K beeper, 1 = 128K AY  ($C242, the probe)
    $FFFE   player 2's character index 0..3   ($C4E3 two players, $C449 one)
    $FFFF   player 1's character index 0..3   ($C42C, from $C7FD)

Each has exactly one reader in the game ($8589, $8560, $9D0A/$BF21, $BE64,
$BE53) -- `python tools/grepbytes.py "3A FF FF"` and friends.

=============================================================================
THE FRONT END'S OWN TIMING MODEL  (manual 0.4 -- it is NOT the game's pass)
=============================================================================
$CC4E is `EI / HALT / RET`: ONE VIDEO FRAME.  The front end installs its own
IM 2 vector ($C27C: I=$FD, a $FD01 table of $EEEE, $EEEE = JP $C824) and that
ISR does the eight-half-row keyboard scan into $C8FB (plus, on a 128K only,
the AY music at $C993).  So the keyboard image the menus test is refreshed
once per frame and every menu loop is paced in whole frames.  MEASURED:

    character picker  loop top $C75B   1.000 frames/iteration
                      key poll $C7BB   14.0 frames  (a 14-step colour ramp)
    control chooser   key poll $C55D   10.0 frames
    player count      poll    $C5CF    UNPACED, ~200 polls/frame, no HALT
                                       (harmless: its two arms clamp at 1 and 2)

A HELD key cannot drive this front end: $C224/$C4F5/$C555/$C7BF wait for a
key to be PRESSED and $C86C/$C859 wait for it to be RELEASED, so anything held
for ever deadlocks in one or the other.  This tool taps SPACE, and holds a
menu key only until the store it causes has happened.

=============================================================================
WHAT THE MENUS ARE, IN ORDER
=============================================================================
    $C1F2  "STOP TAPE AND PRESS SPACE"      $C220, waits on SPACE
    $C22D  the $7FFD paging probe -> $FFFD  (see tools/probe48.py)
    $C254  LDIR $8600 -> $4000              the 6,912-byte loading screen
    $C2CC  credits page                     $CE71, SPACE
    $C2D6  instructions page                $D189, SPACE
    $C2EE  LDIR $A500 -> $4000              THE CHARACTER SELECTION SCREEN
    $C307  "ONE OR TWO PLAYERS" box, $C5CF  5 = ONE, 8 = TWO, SPACE confirms
    $C396  "PLAYER ONE CHOOSE" box
    $C426  CALL $C75B  the picker           5 = next, 8 = previous, SPACE picks
    $C42C  ($FFFF) := ($C7FD)
    $C439  one player?  -> $C43F LD A,R & 3, forced not to equal player 1's,
           ($FFFE) := that.  Two players -> "PLAYER TWO CHOOSE", $C4DD picker
           again (with player 1's choice locked out via $C7FE), $C4E3 stores.
    $C4EB  the CONTROLS screen $D4C2, then $C555 TWICE:
           player 1 -> $FFFC, player 2 -> $FFFB.  6 = next, 7 = previous,
           SPACE selects.  BOTH players always choose, even in a one-player
           game.
    $C520  "PRESS PLAY ON TAPE", DI, RET to the loader at $FF12.

THE ONE-PLAYER / TWO-PLAYER CHOICE NEVER REACHES THE GAME.  $C7FF lives in
block A and dies with it; all it decides is whether $FFFE comes from a second
picker or from `LD A,R`.  One player or two is emergent in the game itself --
whoever presses FIRE is in ($944C/$9451).  `python tools/menugate.py survive`
is the proof: four writes above $FB76, the same four either way.

=============================================================================
THE CHARACTER INDEX -- the question this project could not close
=============================================================================
    0 WARRIOR  "Thor"     top-left,     RED     highlight paper $10, attr $5928
    1 VALKYRIE "Thyra"    bottom-left,  CYAN    highlight paper $28, attr $59A8
    2 WIZARD   "Merlin"   bottom-right, YELLOW  highlight paper $30, attr $59B5
    3 ELF      "Questor"  top-right,    GREEN   highlight paper $20, attr $5936

$C800 is the table of highlight ATTRIBUTE ADDRESSES and $C818 the table of
PAPER bases, both indexed by $C7FD, the byte that becomes $FFFF.  Convert the
addresses to (row,col) and they land on the four quadrants of the screen this
tool renders; the paper bases are the four quadrant colours.  Cross-checked in
the game by $B890, which keys the panel NAME and INK off the tag 8*index:
$7E21 "WARRIOR" ink $42, $7E29 (glyphs) ink $45, $7E33 (glyphs) ink $46,
$7E3B "ELF" ink $44 -- red, cyan, yellow, green in the same order.

Usage:
    python tools/menugate.py all        everything below
    python tools/menugate.py shots      render every menu state to build/fa_*.png
    python tools/menugate.py char       sweep the character picker
    python tools/menugate.py ctrl       sweep the control-method chooser
    python tools/menugate.py two        drive the two-player path
    python tools/menugate.py survive    every write above $FB76, 1P and 2P
    python tools/menugate.py timing     the front end's frame cadence
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import PC, T, IFF                                  # noqa: E402
from probe48 import stage_a                                     # noqa: E402
from keyprobe import KEYS, keymask                              # noqa: E402
import screen as scr                                            # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
OUT = os.path.join(ROOT, 'build')
FRAME = 69888
CHAR = {0: 'WARRIOR  Thor    (red,    top-left)',
        1: 'VALKYRIE Thyra   (cyan,   bottom-left)',
        2: 'WIZARD   Merlin  (yellow, bottom-right)',
        3: 'ELF      Questor (green,  top-right)'}
CTRL = {0: 'SINCLAIR', 1: 'KEMPSTON', 2: 'PROTEK', 3: 'KEYBOARD'}

STAGES = [(0xC220, 'fa_01_stoptape.png', 'STOP TAPE AND PRESS SPACE'),
          (0xC25F, 'fa_02_loading.png', 'the loading screen, $8600 -> $4000'),
          (0xC2D3, 'fa_03_credits.png', 'credits page $CE71'),
          (0xC2DD, 'fa_04_instructions.png', 'instructions page $D189'),
          (0xC2F9, 'fa_05_charscreen.png', 'the character screen, $A500'),
          (0xC386, 'fa_06_players.png', 'ONE OR TWO PLAYERS'),
          (0xC426, 'fa_07_p1choose.png', 'PLAYER ONE CHOOSE'),
          (0xC4F5, 'fa_09_controls.png', 'the CONTROLS screen $D4C2'),
          (0xC520, 'fa_10_pressplay.png', 'PRESS PLAY ON TAPE'),
          (0xFF12, 'fa_11_done.png', 'returned to the loader')]


# --- driving -------------------------------------------------------------
def go(h, targets, limit=60_000_000, tap=None, period=8 * FRAME, hold=None):
    targets = set(targets)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n, phase = 0, -1
    if hold is not None:
        h.ports.release_all()
        sel, bit = KM[hold]
        h.ports.press(sel, keymask(bit))
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return ('target', n)
        if tap is not None:
            ph = (regs[T] // period) & 1
            if ph != phase:
                phase = ph
                h.ports.release_all()
                if ph:
                    sel, bit = KM[tap]
                    h.ports.press(sel, keymask(bit))
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return ('limit', n)


def boot():
    """The machine at the moment the loader executes $FF0F CALL $C1F2, with
    the loader's own return address $FF12 pushed so the front end can end."""
    h = stage_a()
    h.regs[12] = (h.regs[12] - 2) & 0xFFFF
    h.memobj.m[h.regs[12]] = 0x12
    h.memobj.m[h.regs[12] + 1] = 0xFF
    return h


def shot(h, name, scale=3):
    img = scr.render(h.memobj.m, 0x4000, 0x5800)
    img.resize((img.width * scale, img.height * scale)).save(
        os.path.join(OUT, name))


def press_n(h, key, n, store_pc, resume_pc):
    """Press `key` n times.  The menus test a LEVEL, not an edge, so each
    press is held only until the store it causes has happened."""
    for _ in range(n):
        r, _ = go(h, (store_pc,), hold=key, limit=8_000_000)
        assert r == 'target', f'{key} never reached ${store_pc:04X}'
        go(h, (resume_pc,), hold=key, limit=200_000)
        h.ports.release_all()
        go(h, (resume_pc,), limit=200_000)


# --- the measurements ----------------------------------------------------
def cmd_shots():
    print('rendering every front-end state (SPACE tapped, nothing else)')
    h = boot()
    for tgt, name, what in STAGES:
        r, n = go(h, (tgt,), tap='SPACE')
        if r != 'target':
            print(f'  ${tgt:04X} {what}: NOT REACHED'); return
        shot(h, name)
        print(f'  ${tgt:04X}  {h.regs[T]/FRAME:7.1f} frames  {name:26s} {what}')
    m = h.memobj.m
    print('  ' + '  '.join(f'(${a:04X})=${m[a]:02X}'
                           for a in range(0xFFFB, 0x10000)))


def cmd_char():
    print("PLAYER 1's CHARACTER -- '5' steps forward, '8' back, SPACE picks")
    for k in range(4):
        h = boot()
        assert go(h, (0xC426,), tap='SPACE')[0] == 'target'
        press_n(h, '5', k, 0xC7DA, 0xC75B)
        go(h, (0xC79C,), limit=2_000_000)
        shot(h, f'fa_char{k}.png')
        assert go(h, (0xC42C,), tap='SPACE', limit=20_000_000)[0] == 'target'
        h.step(1, interrupts=False)
        v = h.memobj.m[0xFFFF]
        print(f"  '5' x{k}  -> ($FFFF)={v}  {CHAR[v]}   "
              f"[build/fa_char{k}.png]")


def cmd_ctrl():
    print("CONTROL METHOD -- '6' next, '7' previous, SPACE selects")
    for k in range(4):
        h = boot()
        assert go(h, (0xC4F5,), tap='SPACE')[0] == 'target'
        go(h, (0xC558,), limit=2_000_000)
        press_n(h, '6', k, 0xC56C, 0xC558)
        go(h, (0xC55D,), limit=2_000_000)
        shot(h, f'fa_ctrl{k}.png')
        assert go(h, (0xC508,), tap='SPACE', limit=20_000_000)[0] == 'target'
        h.step(1, interrupts=False)
        v = h.memobj.m[0xFFFC]
        print(f"  '6' x{k}  -> ($FFFC)={v}  {CTRL[v]}   "
              f"[build/fa_ctrl{k}.png]")


def cmd_two():
    print("TWO PLAYERS -- '8' picks TWO at $C5CF, then both players pick")
    h = boot()
    assert go(h, (0xC386,), tap='SPACE')[0] == 'target'
    assert go(h, (0xC604,), hold='8', limit=8_000_000)[0] == 'target'
    h.step(1, interrupts=False)
    print(f'   one press of 8: ($C7FF)={h.memobj.m[0xC7FF]}')
    go(h, (0xC5CF,), hold='8', limit=2_000_000)
    h.ports.release_all()
    go(h, (0xC5CF,), limit=400_000)
    shot(h, 'fa_twoplayer.png')
    h.memobj.watch(0xFFFB, 0x10000)
    go(h, (0xC4DD,), tap='SPACE', limit=40_000_000)
    print(f'   player 2 picks from ($C7FD)={h.memobj.m[0xC7FD]}, '
          f'player 1 locked out via ($C7FE)={h.memobj.m[0xC7FE]}')
    go(h, (0xC520,), tap='SPACE', limit=40_000_000)
    h.memobj.unwatch()
    for pc, a, v in h.memobj.log:
        print(f'   (${a:04X}) <- ${v:02X}  from PC=${pc:04X}')
    m = h.memobj.m
    print(f'   p1 {CHAR[m[0xFFFF]]}   p2 {CHAR[m[0xFFFE]]}')
    print(f'   p1 control {CTRL[m[0xFFFC]]}   p2 control {CTRL[m[0xFFFB]]}')


def cmd_survive():
    print('EVERY WRITE ABOVE $FB76 (the only region block C does not cover)')
    for two in (False, True):
        h = boot()
        assert go(h, (0xC386,), tap='SPACE')[0] == 'target'
        if two:
            assert go(h, (0xC604,), hold='8', limit=8_000_000)[0] == 'target'
            h.step(1, interrupts=False)
            go(h, (0xC5CF,), hold='8', limit=2_000_000)
            h.ports.release_all()
            go(h, (0xC5CF,), limit=400_000)
        h.memobj.watch(0xFB77, 0x10000)
        r, n = go(h, (0xFF12,), tap='SPACE', limit=60_000_000)
        h.memobj.unwatch()
        seen = {}
        for pc, a, v in h.memobj.log:
            seen.setdefault(a, []).append((pc, v))
        print(f'  {"TWO" if two else "ONE"} player: {len(seen)} addresses '
              f'written above $FB76 (the probe already wrote $FFFD)')
        for a in sorted(seen):
            print(f'     (${a:04X}) ' +
                  ', '.join(f'${v:02X} from ${pc:04X}' for pc, v in seen[a]))


def _visits(h, pc, n, limit=40_000_000):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    out, k = [], 0
    while len(out) < n and k < limit:
        if regs[PC] == pc:
            out.append(regs[T])
        if mem[regs[PC]] == 0x76 and regs[IFF]:
            h._fast_halt(); k += 1; continue
        p = regs[PC]
        ops[mem[p]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, p)
        k += 1
    return [round((b - a) / FRAME, 3) for a, b in zip(out, out[1:])]


def cmd_timing():
    print('THE FRONT END\'S CADENCE ($CC4E = EI/HALT/RET = one video frame)')
    h = boot(); go(h, (0xC426,), tap='SPACE')
    print(f'  character picker $C75B  {_visits(h, 0xC75B, 8)}')
    h = boot(); go(h, (0xC426,), tap='SPACE')
    print(f'  its key poll    $C7BB  {_visits(h, 0xC7BB, 5)}')
    h = boot(); go(h, (0xC4F5,), tap='SPACE')
    print(f'  control poll    $C55D  {_visits(h, 0xC55D, 6)}')
    h = boot(); go(h, (0xC386,), tap='SPACE')
    g = _visits(h, 0xC5CF, 40)
    print(f'  player count    $C5CF  {g[:5]}  -> UNPACED, '
          f'{1/(sum(g)/len(g)):.0f} polls per frame')


BITS = [(0x01, 'UP'), (0x02, 'DOWN'), (0x04, 'LEFT'), (0x08, 'RIGHT'),
        (0x10, 'FIRE'), (0x20, 'MAGIC')]


def cmd_keymap():
    """The four methods' KEY MAPS, enumerated on the real Z80.

    $9432 is the ONLY caller of $855D, and $855D is the only reader of $FFFC
    and $FFFB.  It writes ONE direction byte per player -- $8427 and $8447 --
    and everything downstream (play, the attract loop, the join, the
    high-score name entry) reads only that byte.  So the whole input model of
    this game is: choose a method, get a key map, get $8427.

    $857E/$85A7 sit OUTSIDE the dispatch: bit 5 MAGIC is CAPS SHIFT for
    player 1 and SPACE for player 2 in ALL FOUR methods.
    """
    import pickle
    from harness import Harness
    h = Harness()
    h.load_state(pickle.load(open(os.path.join(OUT, 'state_48k.pkl'), 'rb')))
    base = h.save_state()
    print('$855D driven with each of the 40 keys held, one at a time')
    for m in range(4):
        rows = {1: {}, 2: {}}
        for name, sel, bit in KEYS:
            h.load_state(base)
            h.poke(0xFFFC, m); h.poke(0xFFFB, m)
            h.poke(0x8427, 0); h.poke(0x8447, 0)
            h.ports.press(sel, keymask(bit))
            h.call(0x855D, interrupts=False)
            for p, addr in ((1, 0x8427), (2, 0x8447)):
                v = h.memobj.m[addr]
                if v:
                    rows[p].setdefault(v, []).append(name)
        p1r, p2r = ((0x85DC, 0x8605), (0x8680, 0x8680),
                    (0x85B3, 0x85B3), (0x862E, 0x8657))[m]
        print(f'  ($FFFC)=($FFFB)={m} {CTRL[m]}   '
              f'p1 ${p1r:04X}  p2 ${p2r:04X}'
              + ('   SAME ROUTINE: both players read one device'
                 if p1r == p2r else ''))
        for p in (1, 2):
            cells = []
            for b, nm in BITS:
                k = rows[p].get(b, ['-'])
                cells.append(f'{nm} {"/".join(k)}')
            print(f'      player {p}: ' + '   '.join(cells))
        if m == 1:
            out = []
            for kb in range(5):
                h.load_state(base)
                h.poke(0xFFFC, m); h.poke(0x8427, 0)
                h.ports.kempston = 1 << kb
                h.call(0x855D, interrupts=False)
                v = h.memobj.m[0x8427]
                out.append(f'bit{kb}->' +
                           '+'.join(n for b, n in BITS if v & b))
            h.ports.kempston = 0
            print('      port $1F (active high): ' + '  '.join(out))


CMDS = {'shots': cmd_shots, 'char': cmd_char, 'ctrl': cmd_ctrl,
        'two': cmd_two, 'survive': cmd_survive, 'timing': cmd_timing,
        'keymap': cmd_keymap}

if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for name in (CMDS if what == 'all' else [what]):
        print('=' * 74)
        CMDS[name]()
        print()
