#!/usr/bin/env python3
"""
attract.py -- drive BLOCK A (the transient front end) and the GAME'S OWN
title/attract loop, and photograph both.

Nothing in this project had ever driven block A end to end, so every number
below is a measurement made by this tool rather than a reading of the code.

WHAT BLOCK A IS.  The loader (`$FF0F CALL $C1F2`) calls it once; block C then
lands on top of it and it is gone.  It owns:

    the "STOP TAPE AND PRESS SPACE" prompt          $C1FD, text inline at $C203
    the 6,912-byte LOADING SCREEN                   $8600 -> $4000 at $C254
    the 48K title TUNE                              $C000, data $C10A/$C173
    two full-screen TEXT PAGES                      $CE71 (credits), $D189 (keys)
    the 6,912-byte TITLE SCREEN                     $A500 -> $4000 at $C2EE
    ONE OR TWO PLAYERS                              $C307 / picker $C5CF
    the CHARACTER picker                            $C426 -> $C75B
    the CONTROL-METHOD page                         $D4C2 / picker $C555
    "PRESS PLAY ON TAPE"                            $C520, then DI / RET

THREE TIMING MODELS IN ONE FRONT END (manual 0.4's warning, in the flesh):

  1. ISR-paced.  $C27C sets IM 2, I=$FD, a 257-byte $EE table and $EEEE:
     JP $C824.  The ISR scans all eight keyboard half-rows into $C8FB..$C902
     (and, on a 128K only, ticks the AY tune).  Every menu reads that buffer,
     never the port.
  2. HALT-paced waits.  $CC4E is `EI / HALT / RET` -- one video frame -- and
     $C5C9 is `CALL $CC4E / DJNZ`, i.e. "wait B frames".  The character picker
     polls its keys once per 14 HALTs; the control picker once per 10.
  3. A DI busy-loop.  The 48K tune at $C000 runs with interrupts OFF and no
     HALT at all; its tempo is loop counts, not frames.

Usage:
    python tools/attract.py blocka      drive block A, write build/fe_*.png
    python tools/attract.py tune        measure the 48K title tune
    python tools/attract.py game        the GAME's title/attract loop ($B47B)
    python tools/attract.py all
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import PC, T, IFF, SP, D, E, FRAME_T                   # noqa: E402
from probe48 import stage_a                                         # noqa: E402
from screen import render                                           # noqa: E402
from keyprobe import KEYS, keymask                                  # noqa: E402

OUT = os.path.join(ROOT, 'build')
KM = {n: (s, b) for n, s, b in KEYS}

# --- block A landmarks -----------------------------------------------------
STOP_TAPE_WAIT = 0xC224     # CALL $C8EA / BIT 0,(IX+7) / JR nz -- wait SPACE
PROBE_COPY = 0xC22D
TUNE_CALL = 0xC2A2          # CALL $C000, the 48K tune
TUNE_BACK = 0xC2A5
PAGE_WAIT = 0xC85E          # a text page is up, waiting for SPACE
TITLE_HOLD = 0xC2F9         # title screen is up, LD B,$32
BOX_OPEN = 0xC307
ONETWO_LOOP = 0xC386
P1_CHOOSE = 0xC410
PICKER = 0xC426
PICK_DONE = 0xC429
CTRL_PAGE = 0xC4EF
CTRL1 = 0xC4FF
CTRL1_DONE = 0xC50B         # after LD ($FFFC),A
CTRL2_DONE = 0xC520         # after LD ($FFFB),A
PRESS_PLAY = 0xC520
FE_RET = 0xC553             # DI / RET, back to the loader at $FF12
LOADER_RET = 0xFF12


def shot(h, name, scale=2):
    im = render(h.memobj.m)
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale))
    p = os.path.join(OUT, name)
    im.save(p)
    return p


def press(h, *names):
    h.ports.release_all()
    for n in names:
        sel, bit = KM[n]
        h.ports.press(sel, keymask(bit))


def runto(h, targets, limit=40_000_000, watchpc=None, tap=None, period=8):
    """Step with interrupts until PC hits a target.  Returns (reason, steps).

    `tap` names a key that is pressed and released alternately every `period`
    video frames.  A HELD key cannot drive this front end: $C224/$C865 wait
    for SPACE to be PRESSED while $C86C waits for it to be RELEASED, so
    anything held for ever deadlocks in one or the other.
    """
    targets = {targets} if isinstance(targets, int) else set(targets)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    per = period * FRAME_T
    n, phase = 0, -1
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return ('target', n)
        if watchpc is not None:
            watchpc(h, pc, n)
        if tap is not None:
            ph = (regs[T] // per) & 1
            if ph != phase:
                phase = ph
                h.ports.release_all()
                if ph:
                    press(h, tap)
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return ('limit', n)


def frames(h, f, watchpc=None):
    """Advance f video frames (T-state accurate; HALTs are jumped)."""
    t0 = h.regs[T]
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while regs[T] - t0 < f * FRAME_T:
        pc = regs[PC]
        if watchpc is not None:
            watchpc(h, pc, n)
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return n


def hold_until(h, key, targets, limit=40_000_000):
    """Hold `key`, run to a target, release.  Used for the SPACE-press waits."""
    press(h, key)
    r = runto(h, targets, limit=limit)
    h.ports.release_all()
    return r


def tap(h, key, hold=6):
    """Press for `hold` frames then release for `hold` frames."""
    press(h, key)
    frames(h, hold)
    h.ports.release_all()
    frames(h, hold)


def boot():
    """A harness at $C1F2 with the loader's own return address $FF12 pushed,
    so the front end's final RET is observable."""
    h = stage_a()
    sp = (h.regs[SP] - 2) & 0xFFFF
    h.memobj.m[sp] = LOADER_RET & 0xFF
    h.memobj.m[sp + 1] = LOADER_RET >> 8
    h.regs[SP] = sp
    return h


class Log:
    def __init__(self, h):
        self.h = h
        self.t0 = h.regs[T]
        self.last = 0.0
        self.rows = []

    def mark(self, label, name=None):
        t = (self.h.regs[T] - self.t0) / FRAME_T
        d = t - self.last
        self.last = t
        pic = ''
        if name:
            shot(self.h, name)
            pic = f'  -> {name}'
        print(f'  {label:<46} {t:9.2f} frames  (+{d:8.2f}){pic}')
        self.rows.append((label, t, d, name))


# =========================================================================
def blocka():
    print('BLOCK A, THE FRONT END, DRIVEN END TO END (48K branch)')
    print('  each row: cumulative video frames since CALL $C1F2, then the delta')
    h = boot()
    lg = Log(h)

    r = runto(h, PROBE_COPY, limit=3_000_000)
    assert r[0] == 'limit', 'the prompt was left without a key press'
    lg.mark('NO KEY: 3,000,000 instructions in the $C224 wait '
            '(no counter, no timeout)', 'fe_00_stoptape.png')

    h2 = boot()
    lg = Log(h2)
    h = h2
    hold_until(h, 'SPACE', PROBE_COPY, limit=5_000_000)
    lg.mark('SPACE pressed -> the $7FFD paging probe')
    runto(h, TUNE_CALL, limit=5_000_000)
    print(f'      the probe stored ($FFFD)=${h.memobj.m[0xFFFD]:02X} '
          f'({"48K/beeper" if h.memobj.m[0xFFFD] == 0 else "128K/AY"})')
    lg.mark('LOADING SCREEN up, tune about to start', 'fe_01_loading.png')

    runto(h, TUNE_BACK, limit=200_000_000)
    lg.mark('title tune finished on its own ($40 marker)')

    runto(h, PAGE_WAIT, limit=5_000_000)
    lg.mark('CREDITS page ($CE71) drawn, waits for SPACE', 'fe_02_credits.png')
    hold_until(h, 'SPACE', 0xC2D3, limit=20_000_000)
    runto(h, PAGE_WAIT, limit=20_000_000)
    lg.mark('KEYS page ($D189) drawn, waits for SPACE', 'fe_03_keys.png')
    hold_until(h, 'SPACE', TITLE_HOLD, limit=20_000_000)
    lg.mark('TITLE SCREEN ($A500) up, LD B,$32', 'fe_04_title.png')

    runto(h, BOX_OPEN, limit=5_000_000)
    lg.mark('50-frame hold done, menu box opened')
    runto(h, ONETWO_LOOP, limit=5_000_000)
    lg.mark('"ONE OR TWO PLAYERS" up, free-running poll',
            'fe_05_oneortwo.png')
    print(f'      ($C7FF) = {h.memobj.m[0xC7FF]}  (1 = ONE, highlighted)')

    tap(h, '8', 6)
    print(f'      key 8 -> ($C7FF) = {h.memobj.m[0xC7FF]}')
    shot(h, 'fe_06_two.png')
    tap(h, '5', 6)
    print(f'      key 5 -> ($C7FF) = {h.memobj.m[0xC7FF]}')

    hold_until(h, 'SPACE', 0xC393, limit=5_000_000)
    lg.mark('SPACE -> "PLAYER ONE CHOOSE"')
    runto(h, P1_CHOOSE, limit=5_000_000)
    lg.mark('"PLAYER ONE CHOOSE" up, LD B,$4B', 'fe_07_p1choose.png')
    runto(h, PICKER, limit=5_000_000)
    lg.mark('box closed, CHARACTER PICKER entered', 'fe_08_picker.png')

    for i in range(4):
        frames(h, 4)
        shot(h, f'fe_09_pulse_{i}.png')
    lg.mark('four 4-frame pulse phases photographed')
    for k in range(3):
        press(h, '5'); frames(h, 16); h.ports.release_all(); frames(h, 16)
        print(f'      key 5 x{k+1}: ($C7FD) = {h.memobj.m[0xC7FD]}')
    shot(h, 'fe_10_picker_moved.png')
    hold_until(h, 'SPACE', 0xC42C, limit=5_000_000)
    lg.mark(f'SPACE picks character {h.memobj.m[0xC7FD]}')
    runto(h, CTRL1, limit=20_000_000, tap='SPACE')
    print(f'      ($FFFF) = {h.memobj.m[0xFFFF]}   ($FFFE) = {h.memobj.m[0xFFFE]}')
    lg.mark('CONTROL page ($D4C2) drawn', 'fe_11_control.png')
    frames(h, 12)
    shot(h, 'fe_12_control_p1.png')
    for k in range(3):
        press(h, '6'); frames(h, 12); h.ports.release_all(); frames(h, 12)
        print(f'      key 6 x{k+1}: ($C808) = {h.memobj.m[0xC808]}')
    shot(h, 'fe_13_control_moved.png')
    lg.mark('control method stepped with key 6 (10-frame poll)')
    r = runto(h, CTRL1_DONE, limit=20_000_000, tap='SPACE')
    print(f'      ($FFFC) player 1 control = {h.memobj.m[0xFFFC]} ({r[0]})')
    lg.mark('SPACE -> player 1 control chosen', 'fe_14_control_p2.png')
    r = runto(h, CTRL2_DONE, limit=20_000_000, tap='SPACE')
    print(f'      ($FFFB) player 2 control = {h.memobj.m[0xFFFB]} ({r[0]})')
    lg.mark('SPACE -> player 2 control chosen')
    h.ports.release_all()
    r = runto(h, LOADER_RET, limit=1_000_000)
    lg.mark(f'"PRESS PLAY ON TAPE", then RET to the loader $FF12 ({r[0]})',
            'fe_15_pressplay.png')
    print(f'      IFF={h.regs[IFF]} at the RET '
          f'($C553 DI -- the game re-enables them itself)')
    m = h.memobj.m
    print('\n  WHAT BLOCK A HANDS TO THE GAME (the five bytes above $FB76):')
    for a, what in ((0xFFFB, 'player 2 control method'),
                    (0xFFFC, 'player 1 control method'),
                    (0xFFFD, 'sound branch  0=48K beeper, 1=128K AY'),
                    (0xFFFE, 'player 2 character'),
                    (0xFFFF, 'player 1 character')):
        print(f'    (${a:04X}) = ${m[a]:02X} = {m[a]:<3}  {what}')
    return h


# =========================================================================
def tune():
    print('\nTHE 48K TITLE TUNE ($C000), MEASURED')
    h = boot()
    hold_until(h, 'SPACE', PROBE_COPY, limit=5_000_000)
    runto(h, TUNE_CALL, limit=5_000_000)
    fetch, ticks, iff = [], [], set()
    h.ports.record_writes = True

    def wp(hh, pc, n):
        if pc == 0xC028:
            fetch.append(hh.regs[E] + 256 * hh.regs[D])
        elif pc == 0xC00D:
            ticks.append(hh.regs[T])
            iff.add(hh.regs[IFF])

    t0 = h.regs[T]
    r = runto(h, TUNE_BACK, limit=200_000_000, watchpc=wp)
    dur = h.regs[T] - t0
    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    lo = [a for a in fetch if a < 0xC172]
    hi = [a for a in fetch if a >= 0xC172]
    w = [x for x in h.ports.writes if (x[1] & 0xFF) == 0xFE]
    print(f'  ran to completion ({r[0]}), returned to ${h.pc():04X}')
    print(f'  duration            {dur} T = {dur/FRAME_T:.2f} video frames '
          f'= {dur/3.5e6:.2f} s')
    print(f'  ticks (notes)       {len(ticks)}')
    print(f'  tick period         mean {sum(gaps)/len(gaps):.0f} T = '
          f'{sum(gaps)/len(gaps)/FRAME_T:.4f} frames '
          f'(min {min(gaps)}, max {max(gaps)})')
    print(f'  channel 1 stream    ${min(lo):04X}..${max(lo):04X}  '
          f'{len(lo)} bytes, the last of them the $40 end marker')
    print(f'  channel 2 stream    ${min(hi):04X}..${max(hi):04X}  '
          f'{len(hi)} bytes (the tune ends on channel 1 first)')
    print(f'  OUT ($FE) writes    {len(w)}  values '
          f'{sorted(set(v for _, _, v in w))}  (bit 4 = speaker, border 0)')
    print(f'  interrupts          IFF seen at every tick: {sorted(iff)} '
          '-- DI at $C00C, EI only on the way out, so it BLOCKS')
    print(f'  the two patched bytes: ($C01A) = ${h.memobj.m[0xC01A]:02X} '
          f'(OUT base: border 0, was $03 = magenta), ($C023) = '
          f'${h.memobj.m[0xC023]:02X} (tempo, was $EE)')

    print('\n  ABORT: hold SPACE from the first tick')
    h2 = boot()
    hold_until(h2, 'SPACE', PROBE_COPY, limit=5_000_000)
    runto(h2, TUNE_CALL, limit=5_000_000)
    t0 = h2.regs[T]
    press(h2, 'SPACE')
    r = runto(h2, TUNE_BACK, limit=20_000_000)
    print(f'    SPACE held -> returned after '
          f'{(h2.regs[T]-t0)/FRAME_T:.2f} frames ({r[0]})')
    print('    $C011 was patched to CALL $C5B9 at $C276, and $C5B9 tests '
          'ONLY SPACE,')
    print('    so on the 48K branch SPACE is the only key that cuts the tune '
          'short.')
    return h


# =========================================================================
PAGE_DRAW = 0xB478          # CALL $869F -- draw the next attract page
JOIN_POLL = 0xB47B          # CALL $9432 -- the join-in poll
SPIN = 0xB487               # LD A,($8497) / OR A / JR nz,$B47B
REWIND = 0xB494             # the "REWIND TAPE TO START OF SIDE 2" prompt
FRAME_CTR = 0x8497
PAGE_CTR = 0x84CB           # (IY+$4C), the 0..3 page number
P1_FLAGS = 0x8434           # bit 7 set = player 1 is NOT in the game

CTRL_NAME = {0: 'SINCLAIR', 1: 'KEMPSTON', 2: 'PROTEK', 3: 'KEYBOARD'}


def game_boot(ctrl=3, char=0, char2=1):
    """A cold boot of the GAME (blocks A+B+C, the $7FFD probe run out of block
    A exactly as tools/boot48.py does), stopped on the first attract page."""
    import boot48
    h = boot48.make('48k', verbose=False)
    h.memobj.m[0xFFFC] = ctrl        # player 1 control method
    h.memobj.m[0xFFFB] = ctrl        # player 2 control method
    h.memobj.m[0xFFFF] = char
    h.memobj.m[0xFFFE] = char2
    r = runto(h, PAGE_DRAW, limit=40_000_000)
    assert r[0] == 'target', 'never reached the attract loop'
    return h


def game():
    print('\nTHE GAME\'S OWN TITLE / ATTRACT LOOP ($B470), MEASURED')
    print('  reached from $B35A NEW GAME -> $B374 CALL $B470, i.e. at COLD')
    print('  BOOT and again after every GAME OVER + name entry.')
    h = game_boot()
    print(f'  cold boot -> ${h.pc():04X} after {h.regs[T]/FRAME_T:.1f} frames')

    # --- the pages -------------------------------------------------------
    print('\n  THE PAGES.  $8767 does INC (IY+$4C) / RES 2 -- a mod-4 counter.')
    stamps = []
    for i in range(6):
        runto(h, JOIN_POLL, limit=10_000_000)
        page = h.memobj.m[PAGE_CTR]
        stamps.append((page, h.regs[T]))
        name = f'ga_page_{i}_ctr{page}.png'
        shot(h, name)
        print(f'    page {i}: ($84CB) = {page}   '
              f't = {h.regs[T]/FRAME_T:9.2f} frames  -> {name}')
        r = runto(h, PAGE_DRAW, limit=10_000_000)
        if r[0] != 'target':
            print('      (did not return to $B478)')
            break
    gaps = [(stamps[i + 1][1] - stamps[i][1]) / FRAME_T
            for i in range(len(stamps) - 1)]
    print(f'    page period: {["%.2f" % g for g in gaps]} video frames')
    print(f'    ($8497) is the ISR frame counter; $B487 spins until it wraps '
          'to 0,\n    so the period is exactly 256 frames minus the page draw.')

    # --- what leaves it --------------------------------------------------
    print('\n  WHAT LEAVES THE ATTRACT LOOP: all 40 keys, all four control '
          'methods.')
    print('  A key "joins" when $9451 RES 7,(IX+$14) clears bit 7 of $8434 '
          '(player 1)\n  or $8454 (player 2).  20 frames held from the same '
          'saved state each time.')
    for ctrl in (0, 1, 2, 3):
        h = game_boot(ctrl=ctrl)
        base = h.save_state()
        joined = {1: [], 2: []}
        for name, sel, bit in KEYS:
            h.load_state(base)
            press(h, name)
            frames(h, 20)
            if not h.memobj.m[0x8434] & 0x80:
                joined[1].append(name)
            if not h.memobj.m[0x8454] & 0x80:
                joined[2].append(name)
        h.load_state(base)
        h.ports.kempston = 0x10          # Kempston FIRE, active high
        frames(h, 20)
        kj = [p for p in (1, 2)
              if not h.memobj.m[0x8434 + 0x20 * (p - 1)] & 0x80]
        print(f'    ($FFFC)=($FFFB)={ctrl} {CTRL_NAME[ctrl]:<9}  '
              f'player 1 joins on {joined[1] or "-"}   '
              f'player 2 joins on {joined[2] or "-"}'
              + (f'   Kempston FIRE bit -> players {kj}' if kj else ''))

    # --- and what it leads to -------------------------------------------
    print('\n  AND WHAT IT LEADS TO.')
    h = game_boot(ctrl=3)
    press(h, 'Z')                                   # player 1 FIRE, KEYBOARD
    r = runto(h, (REWIND, 0xB4B6), limit=20_000_000)
    print(f'    Z (FIRE) -> ${h.pc():04X} ({r[0]}) after '
          f'{r[1]} instructions')
    runto(h, 0xB4AD, limit=5_000_000)
    h.ports.release_all()
    frames(h, 2)
    shot(h, 'ga_rewind.png')
    print('    $B48F BIT 7,(IY+$4D) is CLEAR on a cold boot, so $B494 prints')
    print('    "REWIND TAPE TO START OF SIDE 2" and $B4AD spins on SPACE '
          '-> ga_rewind.png')
    press(h, 'SPACE')
    r = runto(h, 0xB4B6, limit=5_000_000)
    print(f'    SPACE -> ${h.pc():04X} ({r[0]}), back to $B377 and into the '
          'first dungeon')
    return h


# =========================================================================
PAGES = ((0xCE71, 'CREDITS'), (0xD189, 'KEYS'), (0xD4C2, 'CONTROLS'))


def pages():
    """The three full-screen text pages, decoded.

    $C83C's row loop is 32 calls to $C8A4 (one character each, 8x8 from the
    font at $A100, code - $20) and then ONE call to $C877 -- and $C877's DJNZ
    jumps to $C88F, not $C88A, so it reads ONE attribute byte and stores it 32
    times.  A page is therefore 24 x (32 chars + 1 attribute) = 792 bytes, and
    $CE71 + $318 is exactly $D189, the next page.  That is why every row of
    every page is a single colour.
    """
    print('\nTHE TEXT PAGES: 24 rows of (32 characters + 1 row attribute) '
          '= 792 bytes')
    img = open(os.path.join(ROOT, 'build', 'image_a.bin'), 'rb').read()
    for base, name in PAGES:
        rows = []
        a = base
        for _ in range(24):
            chars, attr = img[a:a + 32], img[a + 32]
            a += 33
            rows.append((''.join(chr(c) if 32 <= c < 127 else
                                 ('#' if c >= 0x80 else '.') for c in chars),
                         attr))
        inks = sorted({at & 7 for _, at in rows})
        print(f'  ${base:04X}..${a-1:04X}  {name:<9} {a-base} bytes, '
              f'row inks {inks}')
        for i, (t, at) in enumerate(rows):
            if t.strip():
                print(f'    {i:2d} ${at:02X} |{t}|')
    return PAGES


def sheet():
    """One contact sheet of the whole sequence."""
    from PIL import Image, ImageDraw
    rows = [
        ('fe_00_stoptape.png', '1  STOP TAPE AND PRESS SPACE  $C1FD'),
        ('fe_01_loading.png', '2  LOADING SCREEN + 48K TUNE  $C254 / $C000'),
        ('fe_02_credits.png', '3  CREDITS  $CE71'),
        ('fe_03_keys.png', '4  KEYS  $D189'),
        ('fe_04_title.png', '5  TITLE SCREEN  $A500'),
        ('fe_05_oneortwo.png', '6  ONE OR TWO PLAYERS  $C307'),
        ('fe_07_p1choose.png', '7  PLAYER ONE CHOOSE  $C396'),
        ('fe_08_picker.png', '8  CHARACTER PICKER  $C75B'),
        ('fe_11_control.png', '9  CONTROLS  $D4C2'),
        ('fe_15_pressplay.png', '10 PRESS PLAY ON TAPE  $C520'),
        ('ga_page_0_ctr1.png', '11 ATTRACT 1/4 WARRIOR  $8767'),
        ('ga_page_1_ctr2.png', '12 ATTRACT 2/4 VALKYRIE'),
        ('ga_page_2_ctr3.png', '13 ATTRACT 3/4 WIZARD'),
        ('ga_page_3_ctr0.png', '14 ATTRACT 4/4 ELF'),
        ('ga_rewind.png', '15 REWIND TAPE  $B494'),
    ]
    TW, TH, PAD, CAP, cols = 256, 192, 6, 12, 4
    n = (len(rows) + cols - 1) // cols
    im = Image.new('RGB', (cols * (TW + PAD) + PAD,
                           n * (TH + PAD + CAP) + PAD), (16, 16, 20))
    d = ImageDraw.Draw(im)
    for i, (fn, label) in enumerate(rows):
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            print(f'  missing {fn}')
            continue
        x = PAD + (i % cols) * (TW + PAD)
        y = PAD + (i // cols) * (TH + PAD + CAP)
        im.paste(Image.open(p).resize((TW, TH), Image.LANCZOS), (x, y))
        d.text((x + 1, y + TH + 2), label, fill=(210, 210, 210))
    out = os.path.join(OUT, 'front_end_sequence.png')
    im.save(out)
    print(f'\nwrote {out} ({im.width}x{im.height})')


def loadscreen():
    """The loading screen as a straight asset: $8600, 6,912 bytes."""
    img = open(os.path.join(ROOT, 'build', 'image_a.bin'), 'rb').read()
    mem = bytearray(0x10000)
    mem[0x4000:0x5B00] = img[0x8600:0x8600 + 0x1B00]
    im = render(mem)
    im = im.resize((im.width * 2, im.height * 2))
    p = os.path.join(OUT, 'loading_screen.png')
    im.save(p)
    mem[0x4000:0x5B00] = img[0xA500:0xA500 + 0x1B00]
    im = render(mem)
    im = im.resize((im.width * 2, im.height * 2))
    q = os.path.join(OUT, 'title_screen.png')
    im.save(q)
    print(f'wrote {p} and {q}  (6,912 bytes each, LDIRed to $4000 by '
          '$C254 and $C2EE)')


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('screens', 'all'):
        loadscreen()
    if what in ('pages', 'all'):
        pages()
    if what in ('blocka', 'all'):
        blocka()
    if what in ('tune', 'all'):
        tune()
    if what in ('game', 'all'):
        game()
    if what in ('sheet', 'all'):
        sheet()
