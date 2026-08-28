#!/usr/bin/env python3
"""
blockA.py -- THE FRONT-END HARNESS.  Runs block A, the transient stage the
loader calls at `$FF0F CALL $C1F2`, and reads out what it leaves behind.

WHY A SEPARATE HARNESS.  tools/harness.py builds the POST-loader image: blocks
A, B and C all placed, entry at $8400.  Block C covers $8400..$FB76 and so has
already destroyed block A before the first instruction executes.  That harness
therefore cannot run the front end at all; it can only run the game with
whatever $FFFB..$FFFF happen to hold (the loader stub's padding $2A).

    stage_a()  = ROM + block A at $8600 + the loader stub at $FF00,
                 PC = $C1F2, SP = $5C00, everything else zero.

which is exactly the machine at the instant `$FF0F CALL $C1F2` is executed:
block B and block C have NOT been loaded yet.  The loader's own return address
$FF12 is pushed so that "the front end finished" is a PC comparison and not a
guess.

THE FRONT END'S TIMING MODEL IS NOT THE GAME'S.  The game's loop top is $8503,
visited once per pass, and a pass is several video frames.  Block A instead
HALTs exactly once per screen update, in $CC4E:

    $CC4E  EI / HALT / RET

and its three interactive loops each poll the keyboard AFTER a fixed number of
those HALTs.  The character picker $C75B is the extreme case: $C809 counts
0..13 and the key test at $C7BB is only reached on the pass where it wraps, so
THE PICKER SAMPLES INPUT ONCE EVERY 14 VIDEO FRAMES (0.28 s).  A tap shorter
than that is invisible to it; a key held across the sample is seen once per 14
frames and repeats.  Everything here presses in whole frames for that reason.

Keyboard: block A does NOT use the game's scanner at $B4E8 (that is in block C
and does not exist yet).  It has its own, $C8EA, an eight-half-row scan into
$C8FB..$C902, called once per frame from block A's own IM 2 handler at $C824.
IX is parked at $C8FB throughout, so every key test in the front end reads

    (IX+0)=$FE CAPS Z X C V     (IX+4)=$EF 0 9 8 7 6
    (IX+1)=$FD A S D F G        (IX+5)=$DF P O I U Y
    (IX+2)=$FB Q W E R T        (IX+6)=$BF ENTER L K J H
    (IX+3)=$F7 1 2 3 4 5        (IX+7)=$7F SPACE SYM M N B

Usage:
    python tools/blockA.py map              walk the screens, save PNGs
    python tools/blockA.py chars            drive the picker to each character
    python tools/blockA.py controls         drive the control picker to each method
    python tools/blockA.py two              two-player run
    python tools/blockA.py leaves           full memory diff across the front end
    python tools/blockA.py save             write build/state_frontend.pkl
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, SP, T, IFF, R, rom48, FRAME_T     # noqa: E402
import screen as screenmod                                        # noqa: E402

IMAGE_A = os.path.join(ROOT, 'build', 'image_a.bin')
BUILD = os.path.join(ROOT, 'build')

ENTRY = 0xC1F2                 # $FF0F CALL $C1F2
LOADER_RET = 0xFF12            # the instruction after that CALL

# block A's own key-state buffer, and the half-row each byte holds
KBUF = 0xC8FB
HALFROWS = [
    (0xFE, ['CAPS', 'Z', 'X', 'C', 'V']),
    (0xFD, ['A', 'S', 'D', 'F', 'G']),
    (0xFB, ['Q', 'W', 'E', 'R', 'T']),
    (0xF7, ['1', '2', '3', '4', '5']),
    (0xEF, ['0', '9', '8', '7', '6']),
    (0xDF, ['P', 'O', 'I', 'U', 'Y']),
    (0xBF, ['ENTER', 'L', 'K', 'J', 'H']),
    (0x7F, ['SPACE', 'SYM', 'M', 'N', 'B']),
]
KM = {n: (sel, bit) for sel, names in HALFROWS for bit, n in enumerate(names)}

# the five bytes above block C's top that block A can hand to the game
LEAVE_LO, LEAVE_HI = 0xFFFB, 0x10000
WRITERS = {
    0xC242: '$FFFD  sound branch      (0=48K/beeper, 1=128K/AY) from the $7FFD probe',
    0xC42C: '$FFFF  player 1 character, from ($C7FD)',
    0xC449: '$FFFE  player 2 character, ONE-player game: LD A,R / AND 3',
    0xC4E3: '$FFFE  player 2 character, TWO-player game: from ($C7FD)',
    0xC508: '$FFFC  player 1 control method, from ($C808)',
    0xC51D: '$FFFB  player 2 control method, from ($C808)',
}
CHARNAME = {0: 'char0', 1: 'char1', 2: 'char2', 3: 'char3 (ELF)'}

# front-end working variables, read out of the code
VARS = {0xC7FD: 'character cursor', 0xC7FE: 'forbidden character ($FF=none)',
        0xC7FF: 'number of players', 0xC808: 'control-method cursor',
        0xC809: 'picker flash counter 0..13', 0xC905: 'scroller no-wait flag',
        0xC933: 'title-seen flag'}


def stage_a():
    """The machine at `$FF0F CALL $C1F2`, with the loader's return pushed."""
    h = Harness(deck=False)
    img = bytearray(open(IMAGE_A, 'rb').read())
    img[0:0x4000] = rom48()
    h.memobj.m[:] = img
    r = h.regs
    r[PC] = ENTRY
    r[SP] = 0x5C00                     # $FF00 LD SP,$5C00
    r[IFF] = 0
    r[12] = (r[12] - 2) & 0xFFFF       # push $FF12, the loader's own return
    h.memobj.m[r[12]] = LOADER_RET & 0xFF
    h.memobj.m[r[12] + 1] = LOADER_RET >> 8
    return h


# --- stepping ---------------------------------------------------------------

def step_frames(h, frames, targets=()):
    """Run for `frames` video frames, or until PC hits a target.
    Returns (reason, frames_elapsed)."""
    targets = set(targets)
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    t_end = regs[T] + frames * FRAME_T
    while regs[T] < t_end:
        pc = regs[PC]
        if pc in targets:
            return ('target', (regs[T] - (t_end - frames * FRAME_T)) / FRAME_T)
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return ('frames', frames)


def hold(h, key, frames):
    """Hold `key` down for `frames` whole video frames, then release."""
    sel, bit = KM[key]
    h.ports.press(sel, 0xFF & ~(1 << bit))
    r = step_frames(h, frames)
    h.ports.release_all()
    return r


def tap(h, key, down=4, up=4):
    hold(h, key, down)
    step_frames(h, up)


def step_var(h, key, addr, want, limit=200):
    """Press `key` until (addr) reaches `want`, ONE sample at a time.

    A held key is not a tap: the picker's poll is 14 frames apart and every
    poll that sees the key down steps the cursor again, so "hold for 16 frames"
    can step twice.  We therefore press, watch (addr) change, and release at
    once -- the cursor value itself is the acknowledgement."""
    sel, bit = KM[key]
    for _ in range(limit):
        if h.memobj.m[addr] == want:
            h.ports.release_all()
            return True
        v = h.memobj.m[addr]
        h.ports.press(sel, 0xFF & ~(1 << bit))
        n = 0
        while h.memobj.m[addr] == v and n < 20:
            step_frames(h, 1)
            n += 1
        h.ports.release_all()
        step_frames(h, 16)          # let the next poll see it released
    return h.memobj.m[addr] == want


def press_until(h, key, target, limit=1200):
    """Hold a key (releasing every other frame so that both edge-triggered and
    level-triggered tests can fire) until PC reaches `target`."""
    sel, bit = KM[key]
    n = 0
    while n < limit:
        h.ports.press(sel, 0xFF & ~(1 << bit))
        r = step_frames(h, 2, targets=(target,))
        if r[0] == 'target':
            h.ports.release_all()
            return True
        h.ports.release_all()
        r = step_frames(h, 2, targets=(target,))
        if r[0] == 'target':
            return True
        n += 4
    h.ports.release_all()
    return False


def shot(h, name, scale=3):
    path = os.path.join(BUILD, name)
    img = screenmod.render(h.memobj.m)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), 0)
    img.save(path)
    return path


def show_leaves(h, label=''):
    m = h.memobj.m
    return (f'{label}($FFFB)={m[0xFFFB]:02X} ($FFFC)={m[0xFFFC]:02X} '
            f'($FFFD)={m[0xFFFD]:02X} ($FFFE)={m[0xFFFE]:02X} '
            f'($FFFF)={m[0xFFFF]:02X}')


# --- the scripted walk ------------------------------------------------------

def to_menu(h):
    """From $C1F2 to the "ONE OR TWO PLAYERS" wait loop at $C386.

    $C227 BIT 0,(IX+7) / JR nz,$C224  -- "STOP TAPE AND PRESS SPACE", level
    triggered on SPACE DOWN, scanned by an explicit CALL $C8EA (no interrupts
    yet).  Then the 128K probe, the loading screen, IM 2, the title pages
    ($C2D0 / $C2DA CALL $C83C, each of which ends in $C85E CALL $C86C -- wait
    for SPACE RELEASED -- then $C865 wait for SPACE PRESSED).

    We stop at $C379, not at $C386.  The menu loop

        $C386  CALL $C5CF / BIT 0,(IX+7) / JR nz,$C386

    has NO frame wait in it and leaves the instant SPACE reads down, and SPACE
    is still down when the title page hands over.  On the real machine the
    $C379 LD B,$14 / CALL $C5C9 delay -- 20 HALTs, 0.4 s -- is what gives the
    player time to let go.  Releasing during that delay is what a human does,
    so that is what we do."""
    step_frames(h, 8, targets=(0xC224,))
    if not press_until(h, 'SPACE', 0xC379, limit=4000):
        return False
    h.ports.release_all()
    step_frames(h, 30)                 # through the $C379 delay, into the loop
    return h.pc() != 0xC379


def one_player_run(h, char_taps=0, ctrl1_taps=0, ctrl2_taps=0, players=1,
                   char2_taps=0, shots=None):
    """Drive a whole menu.  Returns True if it returned to the loader."""
    if not to_menu(h):
        return False
    if shots is not None:
        shots['menu'] = shot(h, '%s_1_players.png' % shots['tag'])
    # ONE OR TWO PLAYERS: '8' = more players, '5' = fewer ($C5F1).  The handler
    # is LEVEL triggered and CLAMPED (INC A / CP 3 / JR nz / DEC A), and the
    # loop it sits in has no frame wait, so holding '8' pins the count at 2
    # rather than repeating.
    if players != 1:
        step_var(h, '8', 0xC7FF, players)
    if shots is not None:
        shots['players'] = shot(h, '%s_2_playercount.png' % shots['tag'])
    if not press_until(h, 'SPACE', 0xC426, limit=4000):
        return False
    if shots is not None:
        shots['pick1'] = shot(h, '%s_3_pick1.png' % shots['tag'])
    # PLAYER ONE CHOOSE.  $C7C4 BIT 4,(IX+3) = key '5' -> next character.
    step_frames(h, 20)
    step_var(h, '5', 0xC7FD, char_taps & 3)
    if shots is not None:
        shots['pick1b'] = shot(h, '%s_4_pick1_moved.png' % shots['tag'])
    if not press_until(h, 'SPACE', 0xC429, limit=4000):
        return False
    if players == 2:
        if not press_until(h, 'SPACE', 0xC4DD, limit=4000):
            return False
        if shots is not None:
            shots['pick2'] = shot(h, '%s_5_pick2.png' % shots['tag'])
        step_frames(h, 20)
        step_var(h, '5', 0xC7FD, char2_taps & 3)
        if not press_until(h, 'SPACE', 0xC4E0, limit=4000):
            return False
    # CONTROL METHOD, player 1: $C555, '6' = next, '7' = previous, SPACE = pick
    if not step_frames(h, 400, targets=(0xC4FF,))[0] == 'target':
        return False
    if shots is not None:
        shots['ctrl1'] = shot(h, '%s_6_control1.png' % shots['tag'])
    step_frames(h, 12)
    step_var(h, '6', 0xC808, ctrl1_taps & 3)
    if shots is not None:
        shots['ctrl1b'] = shot(h, '%s_7_control1_moved.png' % shots['tag'])
    if not press_until(h, 'SPACE', 0xC505, limit=4000):
        return False
    # player 2's control method is asked FOR EVERY GAME, one player or two
    if not step_frames(h, 400, targets=(0xC514,))[0] == 'target':
        return False
    step_frames(h, 12)
    step_var(h, '6', 0xC808, ctrl2_taps & 3)
    if not press_until(h, 'SPACE', 0xC51A, limit=4000):
        return False
    r = step_frames(h, 400, targets=(LOADER_RET,))
    if shots is not None:
        shots['end'] = shot(h, '%s_8_pressplay.png' % shots['tag'])
    return r[0] == 'target'


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'map'
    globals()['cmd_' + cmd]()


def cmd_map():
    """Walk the front end and photograph every screen."""
    h = stage_a()
    print('BLOCK A, from $C1F2.  Screens, in order:')
    step_frames(h, 4, targets=(0xC224,))
    print(f'  $C224 "STOP TAPE AND PRESS SPACE"        PC=${h.pc():04X}  '
          f'{shot(h, "fe_0_stoptape.png")}')
    press_until(h, 'SPACE', 0xC245, limit=2000)
    print(f'  $C245 after the 128K probe               ($FFFD)=${h.memobj.m[0xFFFD]:02X}')
    step_frames(h, 20, targets=(0xC25F,))
    print(f'  $C25F loading screen LDIRed to $4000     {shot(h, "fe_1_loadingscreen.png")}')
    step_frames(h, 6, targets=(0xC2D0,))
    press_until(h, 'SPACE', 0xC2D3, limit=4000)
    print(f'  $C2D0 title page 1 ($CE71)               {shot(h, "fe_2_title1.png")}')
    press_until(h, 'SPACE', 0xC2DD, limit=4000)
    print(f'  $C2DA title page 2 ($D189)               {shot(h, "fe_3_title2.png")}')
    press_until(h, 'SPACE', 0xC386, limit=6000)
    step_frames(h, 40)
    print(f'  $C386 ONE OR TWO PLAYERS                 {shot(h, "fe_4_players.png")}')
    press_until(h, 'SPACE', 0xC426, limit=4000)
    step_frames(h, 40)
    print(f'  $C426 PLAYER ONE CHOOSE                  {shot(h, "fe_5_choose1.png")}')
    press_until(h, 'SPACE', 0xC4FF, limit=4000)
    step_frames(h, 40)
    print(f'  $C4FF control method, player 1           {shot(h, "fe_6_control1.png")}')
    press_until(h, 'SPACE', 0xC514, limit=4000)
    step_frames(h, 40)
    print(f'  $C514 control method, player 2           {shot(h, "fe_7_control2.png")}')
    press_until(h, 'SPACE', LOADER_RET, limit=4000)
    print(f'  $FF12 back in the loader                 {shot(h, "fe_8_pressplay.png")}')
    print('  ' + show_leaves(h, 'leaves: '))


def cmd_chars():
    print('THE CHARACTER PICKER $C75B: key 5 steps the cursor, SPACE picks.')
    print('($C7FD is the cursor and it survives into the next game.)')
    for k in range(6):
        h = stage_a()
        h.memobj.watch(LEAVE_LO, LEAVE_HI)
        ok = one_player_run(h, char_taps=k)
        h.memobj.unwatch()
        m = h.memobj.m
        print(f'  5 pressed {k}x -> ($C7FD)={m[0xC7FD]}  ($FFFF)={m[0xFFFF]} '
              f'{CHARNAME.get(m[0xFFFF], "?"):<12} ($FFFE)={m[0xFFFE]} '
              f'($FFFC)={m[0xFFFC]} ($FFFB)={m[0xFFFB]} ($FFFD)={m[0xFFFD]}  '
              f'{"-> loader" if ok else "STUCK at $%04X" % h.pc()}')


def cmd_controls():
    print('THE CONTROL PICKER $C555: key 6 = next, 7 = previous, SPACE picks.')
    for k in range(5):
        h = stage_a()
        ok = one_player_run(h, ctrl1_taps=k, ctrl2_taps=k)
        m = h.memobj.m
        print(f'  6 pressed {k}x -> ($FFFC)={m[0xFFFC]} ($FFFB)={m[0xFFFB]}  '
              f'{"-> loader" if ok else "STUCK at $%04X" % h.pc()}')


def cmd_two():
    """A TWO-player game.  $C42F LD ($C7FE),A parks player 1's choice as the
    FORBIDDEN one and the picker skips it ($C7D4 CP C / JR nz / INC A), so
    player 2 has three reachable characters, never player 1's."""
    print('TWO PLAYERS: every (player 1, player 2) pair the menu can produce.')
    for c1 in range(4):
        row = []
        for c2 in range(4):
            h = stage_a()
            ok = one_player_run(h, players=2, char_taps=c1, char2_taps=c2)
            m = h.memobj.m
            row.append(f'{m[0xFFFE]}' + ('' if ok else '!'))
            if c2 == 0:
                got1 = m[0xFFFF]
        print(f'  p1 asked {c1} -> ($FFFF)={got1};  p2 asked 0/1/2/3 -> '
              f'($FFFE)=' + ' '.join(row))
    print('  (player 2 can never land on player 1\'s index: $C7FE forbids it)')


def cmd_leaves():
    """Everything block A leaves behind that block C does not overwrite."""
    h = stage_a()
    before = bytes(h.memobj.m)
    ok = one_player_run(h)
    after = bytes(h.memobj.m)
    print(f'returned to the loader: {ok}')
    survive = [(a, before[a], after[a]) for a in range(0x4000, 0x10000)
               if before[a] != after[a] and not (0x8400 <= a <= 0xFB76)]
    print(f'{len(survive)} bytes changed OUTSIDE block C\'s $8400..$FB76:')
    runs = []
    for a, b, c in survive:
        if runs and a == runs[-1][1] + 1:
            runs[-1][1] = a
        else:
            runs.append([a, a])
    for lo, hi in runs:
        print(f'  ${lo:04X}..${hi:04X}  ({hi - lo + 1} bytes)')
    return h


def cmd_watch():
    """Every write block A makes ABOVE block C's top, $FB77..$FFFF."""
    for players, ctaps in ((1, 0), (1, 3), (2, 0)):
        h = stage_a()
        h.memobj.watch(0xFB77, 0x10000)
        ok = one_player_run(h, players=players, char_taps=ctaps,
                            char2_taps=1 if players == 2 else 0)
        h.memobj.unwatch()
        acc = {}
        for pc, a, v in h.memobj.log:
            acc.setdefault(a, []).append((pc, v))
        print(f'{players} player(s), player 1 stepped {ctaps}x  (reached loader: {ok})')
        for a in sorted(acc):
            if a < 0xFFF0:
                continue
            for pc, v in acc[a]:
                print(f'  (${a:04X}) <- ${v:02X}  by PC=${pc:04X}   '
                      f'{WRITERS.get(pc, "")}')
        lo = min((a for a in acc if a < 0xFFF0), default=None)
        hi = max((a for a in acc if a < 0xFFF0), default=None)
        if lo is not None:
            print(f'  (bulk region ${lo:04X}..${hi:04X} is block A\'s IM 2 vector '
                  f'table, $C283 LD HL,$FD01 / LD B,$80 / LD (HL),E/D)')
        print()


def cmd_boot():
    """THE WHOLE LOADER: front end, then blocks B and C, then the game.

    This is the first time in this project that the game has been entered the
    way the tape enters it.  The loader's own tail is

        $FF12  65,536-iteration delay, border 0     (time only)
        $FF20  block B -> $73DA, $0B41              (a tape load)
        $FF2C  block C -> $8400, $7777              (a tape load)
        $FF38  JP $8400

    and the two loads are performed here by placing the blocks, with the same
    length assertions tools/mkimage.py makes, because the harness has no tape
    signal model for side 1."""
    import mkimage
    char = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    method = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    h = stage_a()
    ok = one_player_run(h, char_taps=char, ctrl1_taps=method, ctrl2_taps=method)
    assert ok, f'front end did not return to the loader (PC=${h.pc():04X})'
    print('front end done.  ' + show_leaves(h))
    for spec in (mkimage.BLOCK_B, mkimage.BLOCK_C):
        dest, ln, idx = spec
        data = mkimage.body(idx)
        assert len(data) == ln
        h.memobj.m[dest:dest + ln] = data
    print(f'blocks B and C placed; block C covers $8400..${0x8400+0x7777-1:04X}')
    surv = show_leaves(h, 'after block C: ')
    print('  ' + surv)
    h.regs[PC] = 0x8400
    h.regs[SP] = 0xFFFB
    h.regs[IFF] = 0
    h.regs[27] = 1
    from harness import TapeDeck, tape_blocks, SIDE2
    h.deck = TapeDeck(tape_blocks(SIDE2), 'side2')
    # THE KEY SCRIPT DEPENDS ON THE CONTROL METHOD WE JUST CHOSE.  $B374 CALL
    # $B470 is the attract loop and it waits for player 1's FIRE; $B4AD waits
    # for SPACE.  FIRE is a different key in each arm of $8560's dispatch:
    #   0 SINCLAIR  '0'   ($85FD BIT 0,(IY+4))
    #   1 KEMPSTON  bit 4 of IN A,($1F)   ($8680)
    #   2 PROTEK    '0'   ($85D4 BIT 0,(IY+4))
    #   3 KEYBOARD  'Z'   ($864F BIT 1,(IY+0))
    # This is exactly why every existing key script in the project holds Z: it
    # was written for the arm the stale $FFFC = $2A fell through to.
    fire = {0: '0', 1: None, 2: '0', 3: 'Z'}[method]
    if fire:
        sel, bit = KM[fire]
        h.ports.press(sel, 0xFF & ~(1 << bit))
    else:
        h.ports.kempston = 0x10
    sel, bit = KM['SPACE']
    h.ports.press(sel, 0xFF & ~(1 << bit))
    print(f'  holding FIRE ({fire or "Kempston bit 4"}) and SPACE')
    reason, n = h.run_until((0x8503,), limit=60_000_000)
    h.ports.release_all()
    print(f'game: reached ${h.pc():04X} ({reason}, {n} instructions), '
          f'tape stops {h.tape_stops}')
    print(f'  ($8433) shot tag = ${h.memobj.m[0x8433]:02X}   '
          f'($8435) attribute = ${h.memobj.m[0x8435]:02X}')
    p = os.path.join(BUILD, f'state_frontend_c{char}_m{method}.pkl')
    pickle.dump(h.save_state(), open(p, 'wb'))
    print(f'  wrote {p}')
    print(f'  {shot(h, f"fe_9_play_c{char}_m{method}.png")}')
    return h


def cmd_inputmap():
    """ENUMERATE the four control methods $FFFC selects, by measurement.

    $855D CALL $B4E8 / $8560 LD A,($FFFC) and a four-way dispatch; the arm
    writes a bitmap to ($8427) for player 1 (UP 1, DOWN 2, LEFT 4, RIGHT 8,
    FIRE $10) and $857E ORs in MAGIC $20 from CAPS.  $8589 does the same for
    player 2 from ($FFFB) into ($8447), MAGIC from SPACE.

    We take the live 48K state, poke $FFFC/$FFFB, hold one key, run one pass
    and read the byte.  That is the whole input model, measured rather than
    read off the menu art."""
    import pickle as pk
    from harness import Harness as H2
    NAMES = {0: 'SINCLAIR', 1: 'KEMPSTON', 2: 'PROTEK', 3: 'KEYBOARD'}
    BITS = [(1, 'UP'), (2, 'DOWN'), (4, 'LEFT'), (8, 'RIGHT'),
            (0x10, 'FIRE'), (0x20, 'MAGIC')]
    base = pk.load(open(os.path.join(BUILD, 'state_48k.pkl'), 'rb'))
    for who, sel_addr, out_addr in ((1, 0xFFFC, 0x8427), (2, 0xFFFB, 0x8447)):
        print(f'PLAYER {who}: ($%04X) selects, result in ($%04X)'
              % (sel_addr, out_addr))
        for method in range(4):
            found = {}
            for key in KM:
                h = H2()
                h.load_state(base)
                h.memobj.m[sel_addr] = method
                sel, bit = KM[key]
                h.ports.press(sel, 0xFF & ~(1 << bit))
                for _ in range(2):
                    h.step(1)
                    h.run_until((0x8503,), limit=400_000)
                v = h.memobj.m[out_addr]
                if v:
                    for mask, nm in BITS:
                        if v & mask:
                            found.setdefault(nm, []).append(key)
            # Kempston is a port, not a key
            kem = ''
            if method == 1:
                h = H2()
                h.load_state(base)
                h.memobj.m[sel_addr] = 1
                bits = []
                for b in range(5):
                    h2 = H2(); h2.load_state(base)
                    h2.memobj.m[sel_addr] = 1
                    h2.ports.kempston = 1 << b
                    for _ in range(2):
                        h2.step(1)
                        h2.run_until((0x8503,), limit=400_000)
                    bits.append(f'${1<<b:02X}->${h2.memobj.m[out_addr]:02X}')
                kem = '   IN($1F) ' + ' '.join(bits)
            print(f'  {method} {NAMES[method]:<9} '
                  + '  '.join(f'{nm}={"/".join(ks)}' for nm, ks in found.items())
                  + kem)


def cmd_shots():
    """Photograph each pick, at the instant the picker accepts SPACE."""
    from PIL import Image
    tiles = []
    for k in range(4):
        h = stage_a()
        assert to_menu(h)
        assert press_until(h, 'SPACE', 0xC426, limit=4000)
        step_frames(h, 20)
        step_var(h, '5', 0xC7FD, k)
        step_frames(h, 30)
        p = shot(h, f'fe_choose1_index{k}.png')
        tiles.append(Image.open(p).crop((0, 0, 768, 576)).resize((384, 288)))
        print(f'  character cursor ($C7FD) = {k}  -> {p}')
    grid = Image.new('RGB', (768, 576))
    for i, t in enumerate(tiles):
        grid.paste(t, ((i % 2) * 384, (i // 2) * 288))
    grid.save(os.path.join(BUILD, 'fe_choose1_all4.png'))
    print('  ' + os.path.join(BUILD, 'fe_choose1_all4.png'))

    tiles = []
    for k in range(4):
        h = stage_a()
        assert to_menu(h)
        assert press_until(h, 'SPACE', 0xC426, limit=4000)
        assert press_until(h, 'SPACE', 0xC4FF, limit=4000)
        step_frames(h, 12)
        step_var(h, '6', 0xC808, k)
        step_frames(h, 8)
        p = shot(h, f'fe_control_index{k}.png')
        tiles.append(Image.open(p).crop((0, 150, 768, 320)))
        print(f'  control cursor ($C808) = {k}  -> {p}')
    grid = Image.new('RGB', (768, 680))
    for i, t in enumerate(tiles):
        grid.paste(t, (0, i * 170))
    grid.save(os.path.join(BUILD, 'fe_control_all4.png'))
    print('  ' + os.path.join(BUILD, 'fe_control_all4.png'))

    h = stage_a()
    assert to_menu(h)
    step_var(h, '8', 0xC7FF, 2)
    step_frames(h, 4)
    print('  TWO PLAYERS selected -> ' + shot(h, 'fe_players_two.png'))


def cmd_p2draw():
    """$C43F LD A,R / AND 3 / CP C / JR nz / INC A / AND 3 -- player 2's
    character in a ONE-player game.

    Two things are worth separating.  The GUARD's reachable outputs are a
    closed enumeration; the DRAW itself is the refresh register, and the only
    honest way to see whether it is a draw in practice is to vary what the
    player did before it.  Here we vary the dwell in the picker by 0..15
    frames and read the result."""
    print('(a) the guard, enumerated over all 256 values of R:')
    for c in range(4):
        hist = {}
        for r in range(256):
            a = r & 3
            if a == c:
                a = (a + 1) & 3
            hist[a] = hist.get(a, 0) + 1
        print(f'    player 1 = {c}: ' + '  '.join(
            f'{k}->{v/256:.2f}' for k, v in sorted(hist.items())))
    print('(b) MEASURED, varying only how long the player dwells in the picker:')
    for c1 in range(4):
        got = []
        for extra in range(16):
            h = stage_a()
            if not to_menu(h):
                continue
            press_until(h, 'SPACE', 0xC426, limit=4000)
            step_frames(h, 20)
            step_var(h, '5', 0xC7FD, c1)
            step_frames(h, extra)
            press_until(h, 'SPACE', 0xC429, limit=4000)
            step_frames(h, 60, targets=(0xC44C,))
            got.append(h.memobj.m[0xFFFE])
        print(f'    player 1 = {c1}: ' + ' '.join(str(g) for g in got)
              + f'   ({len(set(got))} distinct)')


def cmd_save():
    """Two states the rest of the project can use.

    build/state_frontend_menu.pkl -- PC = $C426, the instant the player-1
        character picker is entered, everything before it done for real.  Load
        it, drive $C75B with key 5 and SPACE, and you are choosing a character
        on the original.
    build/state_frontend_done.pkl -- PC = $FF12, the front end finished and
        returned to the loader, with $FFFB..$FFFF written.  From here
        tools/blockA.py's `boot` places blocks B and C and jumps to $8400."""
    h = stage_a()
    assert to_menu(h)
    assert press_until(h, 'SPACE', 0xC426, limit=4000)
    p1 = os.path.join(BUILD, 'state_frontend_menu.pkl')
    pickle.dump(h.save_state(), open(p1, 'wb'))
    print(f'wrote {p1}   PC=${h.pc():04X}  ($C7FD)={h.memobj.m[0xC7FD]} '
          f'($C7FF)={h.memobj.m[0xC7FF]}')
    h = stage_a()
    ok = one_player_run(h)
    assert ok, 'did not reach the loader'
    p2 = os.path.join(BUILD, 'state_frontend_done.pkl')
    pickle.dump(h.save_state(), open(p2, 'wb'))
    print(f'wrote {p2}   PC=${h.pc():04X}  ' + show_leaves(h))


if __name__ == '__main__':
    main()
