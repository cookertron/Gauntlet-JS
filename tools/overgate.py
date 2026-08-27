#!/usr/bin/env python3
"""
overgate.py -- drive the REAL Z80 through everything that happens AFTER the
main loop returns: the post-death name-entry loop, the join/rejoin, the
high-score table and the restart.  Same shape as deathgate.py / shotgate.py: it
never loads the engine, it only asks the original.

    python tools/overgate.py all
    python tools/overgate.py exits      the FOUR ways out of $8503, enumerated
    python tools/overgate.py loop       the $B3B9 loop: rate, hot PCs, what it draws
    python tools/overgate.py name       the letter picker: alphabet, keys, repeat rate
    python tools/overgate.py cursor     the cursor is an attribute swap, not a flash
    python tools/overgate.py hiscore    the four ranked tables and the insertion sort
    python tools/overgate.py attract    the attract/join screen and its 4-page cycle
    python tools/overgate.py restart    game over -> new game -> live again, timed
    python tools/overgate.py rejoin     the 2-player rejoin INSIDE the live main loop
    python tools/overgate.py block      every stall in the path, in video frames
    python tools/overgate.py lives      the full 64K diff: there is no life counter

THE SHAPE.  $8556 BIT 7,(IY-2) is the only way out of the main loop, and the
caller is $B391 CALL $8503, inside $B35A's NEW GAME:

    $B391  CALL $8503                          the game
    $B394  BIT 6,(IY-2) / JP nz,$B3B6          -> JP $0000, a hard reset.  Bit 6
                                               is TESTED here and SET NOWHERE in
                                               the image: this arm is dead code.
    $B39B  INC (IY+$12) / CALL $B6DA           the SPIN: bump the pass counter and
    $B3A1  ($842B | $844B) & $3F -> loop       run the HUD round robin until both
                                               players' dirty flags have flushed
    $B3AB  ($8434 & $8454) bit 7 -> $B3B9      BOTH dead: game over
    $B3B4  else JR $B381                       the next dungeon
    $B3B9  CALL $9432                          THE POST-DEATH LOOP (name entry)
    $B3BC  ($842E | $844E) & $FE -> loop       until every dead player's (IX+14)
                                               has gone back to 0 or 1
    $B3C6  ($84BB) -> ($84C5) / DI / JP $B35A  a brand-new game.  No continue.
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, FRAME_T, TAPE_CALL_PC   # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503
OVER_TOP = 0xB3B9
P1 = dict(up='1', down='Q', left='S', right='D', fire='Z', shift='CAPS')
P2 = dict(up='8', down='I', left='K', right='L', fire='M', shift='SPACE')

HS_TABLE = 0x8826          # 4 characters x 10 entries x 6 bytes
HS_STAGE = 0x8916          # the 6-byte packed key $869F builds
PEND = 0x7F2B              # the pending records, 8 bytes each, count at $84C6
NAMEBUF = 0x7F2E           # pending record 0's three name bytes
CURCOL = 0x8439            # p1+$19, the name-entry cursor column
CHARNAME = {0: 'WARRIOR', 1: 'VALKYRIE', 2: 'WIZARD', 3: 'ELF'}


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def press(h, name):
    sel, bit = KM[name]
    h.ports.press(sel, keymask(bit))


def run_until_pc(h, targets, limit=20_000_000):
    if isinstance(targets, int):
        targets = (targets,)
    targets = set(targets)
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0, n = regs[T], 0
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return (pc, regs[T] - t0, n)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return (None, regs[T] - t0, n)


def profile_n(h, ninstr):
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    hist, n, t0 = collections.Counter(), 0, regs[T]
    while n < ninstr:
        pc = regs[PC]
        hist[pc] += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return hist, regs[T] - t0


def die(h, tag=0x18, score=None):
    """kill player 1 and stop at the top of the post-death loop.

    tag is the CHARACTER byte $8433: $18 = the elf, i.e. what the port repairs
    $FFFF to.  The captured state's own $20 is not a reachable game state and
    it silently disables the high-score table -- see cmd_hiscore."""
    run_until_pc(h, LOOP_TOP)
    h.poke(0x8433, tag)
    if score is not None:
        h.poke(0x8424, *score)
    h.poke(0x8422, 0x00, 0x00)
    run_until_pc(h, 0x855C)
    run_until_pc(h, OVER_TOP)


def pname(m):
    return ''.join(chr(b) for b in m[NAMEBUF:NAMEBUF + 3])


def spin_frames(h, nframes, stop=(0xB3C6,)):
    """run the $B3B9 loop for nframes VIDEO frames, or until a stop pc."""
    m = h.memobj.m
    seen, last = 0, m[0x8497]
    while seen < nframes:
        pc, _, _ = run_until_pc(h, (OVER_TOP,) + tuple(stop))
        if pc in stop:
            return pc
        cur = m[0x8497]
        seen += (cur - last) & 0xFF
        last = cur
    return None


def step_letter(h, key):
    """one accepted letter/column step: the input gate is 8 video frames."""
    press(h, P1[key]); spin_frames(h, 8); h.ports.release_all(); spin_frames(h, 2)


def type_name(h, m, letters):
    for col, ch in enumerate(letters):
        guard = 0
        while m[NAMEBUF + col] != ord(ch) and guard < 40:
            step_letter(h, 'up'); guard += 1
        if col < 2:
            step_letter(h, 'right')


# ---------------------------------------------------------------- exits
def cmd_exits():
    print("""  $8556 BIT 7,(IY-2) is the ONLY exit from $8503, and $847D bit 7 is set from
  exactly two places.  Enumerated with tools/grepbytes.py over the live image:

     $94D1  SET 7,(IY-2)   reached from $94AE ($8537, every pass) when
                           ($842B & $844B) bit 7 -- BOTH players' "level over"
                           bit -- or when $847D bit 3 is already set
     $8B22  SET 7,(IY-2)   the two-digit BCD countdown $84B6 (drawn at $50AF,
                           row 20 col 15, ticked every 12 of $84B7) reaching 00.
                           It sets bit 3 as well, which $94AE then honours.
                           Never fires in dungeon 1: $84B6 is not counted here.

  and $847D bit 7 is cleared only by $B3D7 LD ($847D),A with A = 0, in the level
  setup.  $847D bit 6, tested at $B394 and worth a JP $0000 (a hard machine
  reset), is SET NOWHERE: 22 (IY-2) bit sites in the image, none of them bit 6,
  and the only whole-byte write is that A = 0.  That arm is unreachable.

  So a player-1 death in a ONE-player game ends the level because player 2's
  $844B already has bit 7 set.  Four routes to the same bit:""")
    h = boot(); m = h.memobj.m
    run_until_pc(h, LOOP_TOP)
    print('     at the loop top: $842B=%02X $844B=%02X $847D=%02X'
          % (m[0x842B], m[0x844B], m[0x847D]))
    h.poke(0x8422, 0x00, 0x00)
    for i in range(3):
        pc, dt, n = run_until_pc(h, (LOOP_TOP, 0x855C))
        print('     pass %d  $842B=%02X $842E=%02X $8434=%02X $847D=%02X  %.2f frames  %s'
              % (i + 1, m[0x842B], m[0x842E], m[0x8434], m[0x847D], dt / FRAME_T,
                 'RETURN at $855C' if pc == 0x855C else ''))
        if pc == 0x855C:
            break
    print('     the $B39B spin then flushes the HUD dirty bits:')
    for i in range(8):
        pc, dt, n = run_until_pc(h, (0xB39B, OVER_TOP, 0xB381))
        print('       %s ctr=%d $842B=%02X  %.3f frames  %d instructions'
              % ('$%04X' % pc, m[0x8491], m[0x842B], dt / FRAME_T, n))
        if pc != 0xB39B:
            break


# ---------------------------------------------------------------- loop
def cmd_loop():
    h = boot(); m = h.memobj.m
    die(h)
    hist, dt = profile_n(h, 200_000)
    its = hist[OVER_TOP]
    fr = dt / FRAME_T
    print('  $B3B9 ran %d times in %.1f video frames' % (its, fr))
    print('     %.2f iterations per video frame, %.0f instructions each'
          % (its / fr, 200_000 / its))
    print('     it is NOT frame-paced: no HALT, no counter -- it spins flat out.')
    print('     per iteration: %.2f keyboard scans ($B4E8), %.2f glyph draws ($B6C0),'
          % (hist[0xB4E8] / its, hist[0xB6C0] / its))
    print('                    %.2f input dispatches ($855D), %.2f name-entry ticks ($930C)'
          % (hist[0x855D] / its, hist[0x930C] / its))
    print('     hot PCs: %s'
          % ', '.join('$%04X x%d' % (p, c) for p, c in hist.most_common(4)))
    print('     $B6D0 (the glyph inner loop) %d = 8 per iteration -- ONE character,'
          % hist[0xB6D0])
    print('     the one under the cursor, is redrawn every time round.')


# ---------------------------------------------------------------- name
def cmd_name():
    h = boot(); m = h.memobj.m
    die(h)
    print('  the field is the HUD panel, screen row 20 columns 6..8 (p1) / 23..25 (p2)')
    print('  ($92A6 LD DE,$5086 / LD E,$97; $B694 blanks 15 cells, then three $41s)')
    print('  it starts as "%s", cursor column %d' % (pname(m), m[CURCOL]))
    print()
    print('  --- UP, held, sampled every video frame ---')
    press(h, P1['up'])
    seq, prev = [m[NAMEBUF]], m[NAMEBUF]
    gaps, lastf, tot = [], 0, 0
    last = m[0x8497]
    while tot < 250:
        run_until_pc(h, OVER_TOP)
        cur = m[0x8497]
        tot += (cur - last) & 0xFF
        last = cur
        if m[NAMEBUF] != prev:
            prev = m[NAMEBUF]; seq.append(prev); gaps.append(tot - lastf); lastf = tot
    h.ports.release_all()
    print('     %s' % ' '.join(('SP' if b == 0x20 else chr(b)) for b in seq))
    print('     %d steps, %d distinct -> the alphabet is A..Z plus SPACE, 27 slots,'
          % (len(seq) - 1, len(set(seq))))
    print('     and SPACE sits BETWEEN Z and A ($937B INC A / CP $5B -> $40;')
    print('      $9395 CP $40 -> $20).  step gaps in video frames: %s'
          % collections.Counter(gaps[1:]).most_common())
    print()
    print('  --- one 8-frame press per key ---')
    for key, lbl in [('down', 'DOWN'), ('down', 'DOWN'), ('right', 'RIGHT'),
                     ('up', 'UP'), ('right', 'RIGHT'), ('right', 'RIGHT (clamped)'),
                     ('left', 'LEFT'), ('left', 'LEFT'), ('left', 'LEFT (clamped)'),
                     ('fire', 'FIRE')]:
        step_letter(h, key)
        print('     %-16s name=%-4s cursor column %d' % (lbl, pname(m), m[CURCOL]))
    print('     FIRE does nothing: $9440 tests it only AFTER (IX+14) is back to 0.')
    print()
    print('  --- SHIFT commits ---')
    press(h, P1['shift'])
    r = spin_frames(h, 30)
    h.ports.release_all()
    print('     exit pc=$%04X on the SAME iteration; $842E %02X, so $B3BC falls'
          % (r, m[0x842E]))
    print('     through to $B3C6.  There is no window in which FIRE could rejoin.')


# ---------------------------------------------------------------- cursor
def cmd_cursor():
    h = boot(); m = h.memobj.m
    die(h)
    spin_frames(h, 3)
    print('  row 20 attrs $5A86..$5A8A  %s   <- the name field'
          % ' '.join('%02X' % b for b in m[0x5A86:0x5A8B]))
    print('  row 21 attrs $5AA6..$5AAA  %s   <- what $9350 READS'
          % ' '.join('%02X' % b for b in m[0x5AA6:0x5AAB]))
    print('  $9358 swaps the ink and paper nibbles of the cell ONE ROW BELOW and')
    print('  ORs $40: $44 -> $60.  Row 21 is static panel art, so the highlight is')
    print('  a STABLE inverse-video block, not a flash -- worth saying because the')
    print('  write happens ~27 times per video frame and a naive reading of')
    print('  "swap the attribute" gives a 1.4 kHz flicker.')
    seen = collections.Counter()
    for _ in range(60):
        run_until_pc(h, OVER_TOP)
        seen[m[0x5A86]] += 1
    print('  $5A86 over 60 consecutive loop iterations: %s' % dict(seen))
    for col in (1, 2, 0):
        guard = 0
        while m[CURCOL] != col and guard < 8:
            step_letter(h, 'right' if m[CURCOL] < col else 'left'); guard += 1
        spin_frames(h, 2)
        print('  cursor column %d -> row 20 attrs %s'
              % (col, ' '.join('%02X' % b for b in m[0x5A86:0x5A8A])))


# ---------------------------------------------------------------- hiscore
def unpack(ent):
    """the 6-byte packed key -> (millions, name, score)"""
    mm, nm = 0, ''
    for i in range(3):
        b = ent[i]
        mm = (mm << 2) | (b >> 6)
        v = b & 0x3F
        nm += ' ' if v == 0 else chr(0x40 + v)
    return mm, nm, '%02X%02X%02X' % (ent[3], ent[4], ent[5])


def show_table(m, c, rows=4):
    base = HS_TABLE + 0x3C * c
    out = []
    for k in range(10):
        mm, nm, sc = unpack(m[base + 6 * k: base + 6 * k + 6])
        out.append('%s %s%s' % (nm, ('%d' % mm) if mm else ' ', sc))
    print('     %-9s $%04X  %s' % (CHARNAME[c], base, ' | '.join(out[:rows])))
    if rows < 10:
        print('     %-9s        %s' % ('', ' | '.join(out[rows:rows + 3])))


def cmd_hiscore():
    h = boot(); m = h.memobj.m
    print('  the RANKED tables are four 60-byte blocks at $8826, one per character,')
    print('  10 entries of 6 bytes each; $8818 indexes them as $87EA + $3C*(tag/8+1).')
    print('  $8916, immediately after the last block, is the 6-byte staging key.')
    print('  as shipped:')
    for c in range(4):
        show_table(m, c, rows=3)
    print()
    print('  the entry is packed by $8757: byte = (2 bits of the MILLIONS counter')
    print('  $84C8) << 6 | (letter - $40, SPACE -> 0), three of them, then the 3')
    print('  score bytes verbatim.  $86F4 compares the AND $C0 fields first and')
    print('  $8709 the three score bytes, so it is a big-endian sort on')
    print('  (millions, score) and the letters never affect the ranking.')
    print()
    print('  --- one game, elf, score 034567, name JOE ---')
    die(h, tag=0x18, score=(0x03, 0x45, 0x67))
    print('     pending record $%04X: %s   $84C6=%d'
          % (PEND, ' '.join('%02X' % b for b in m[PEND:PEND + 8]), m[0x84C6]))
    print('     = score[3], name[3], the CHARACTER byte $8433, the millions byte $84C8')
    type_name(h, m, 'JOE')
    print('     typed %r' % pname(m))
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    run_until_pc(h, 0x869F)
    before = bytes(m[HS_TABLE:HS_TABLE + 0x100])
    pc, dt, n = run_until_pc(h, 0x87A8)
    after = bytes(m[HS_TABLE:HS_TABLE + 0x100])
    print('     $869F..$87A8 (sort + draw): %.2f frames, %d bytes of table changed,'
          % (dt / FRAME_T, sum(1 for a, b in zip(before, after) if a != b)))
    print('     $84C6 consumed -> %d ($872F)' % m[0x84C6])
    show_table(m, 3, rows=3)
    print()
    print('  --- the same with the CAPTURED tag $20 (the $FFFF boot bug) ---')
    h2 = boot(); m2 = h2.memobj.m
    die(h2, tag=0x20, score=(0x03, 0x45, 0x67))
    press(h2, P1['shift']); spin_frames(h2, 30); h2.ports.release_all()
    run_until_pc(h2, 0x869F)
    b_tab = bytes(m2[HS_TABLE:HS_STAGE])
    b_code = bytes(m2[HS_STAGE + 6:HS_STAGE + 0x3C])
    run_until_pc(h2, 0x87A8)
    print('     tag $20 -> $8818 gives $8826 + $3C*4 = $8916, the STAGING BUFFER.')
    print('     bytes changed in the four real tables $8826..$8915: %d'
          % sum(1 for a, b in zip(b_tab, bytes(m2[HS_TABLE:HS_STAGE])) if a != b))
    print('     bytes changed in the CODE at $891C..$8951 it walks instead: %d'
          % sum(1 for a, b in zip(b_code,
                                  bytes(m2[HS_STAGE + 6:HS_STAGE + 0x3C])) if a != b))
    print('     -> the score is silently discarded, and nothing is corrupted:')
    print('     slot 0 is the key compared with itself (equal), and slots 1..9 are')
    print('     the CODE at $891C.., whose AND $C0 fields are $C0, which no')
    print('     reachable key can beat.  The boot bug silently kills the table.')


# ---------------------------------------------------------------- attract
def cmd_attract():
    h = boot(); m = h.memobj.m
    die(h)
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    pc, dt, n = run_until_pc(h, 0xB35A)
    print('  $B3C6 -> $B35A (NEW GAME) after %.2f frames' % (dt / FRAME_T))
    print('     $B35A clears $7F1B..$7F2A and $84C8/$84C9, sets $8403 = $842C =')
    print('     $844C = 1 (dungeon 1) and $84BA = 2 + rand&3, then $B374 CALL $B470.')
    print('  the attract loop $B470: draw the table, then poll until $8497 wraps:')
    for i in range(5):
        pc, dt, n = run_until_pc(h, 0x8767, limit=60_000_000)
        run_until_pc(h, 0xB47B)
        print('     redraw %d: %7.1f frames since the last, $84CB=%d -> %s page'
              % (i, dt / FRAME_T, m[0x84CB], CHARNAME[(m[0x84CB] - 1) & 3]))
    print('     = one page every 251.8 frames (5.03 s), cycling all four characters.')
    print('     both players are DEAD ($8434/$8454 bit 7) and both halves of the')
    print('     panel say PRESS FIRE; $9440 is what polls it.')
    print()
    print('  FIRE joins:')
    before = bytes(m[0x8420:0x8440])
    press(h, P1['fire'])
    pc, dt, n = run_until_pc(h, (0xB48F, 0xB4AD), limit=60_000_000)
    h.ports.release_all()
    print('     -> $%04X after %.2f frames' % (pc, dt / FRAME_T))
    print('     p1 before %s' % ' '.join('%02X' % b for b in before[:16]))
    print('     p1 after  %s' % ' '.join('%02X' % b for b in m[0x8420:0x8430]))
    print('     $946E zeroes (IX+3..6), (IX+8..10), writes $FF to (IX+$10) and')
    print('     health = BCD 2000 at (IX+2).  $9484 means to free the shot but')
    print('     writes its X coordinate, not its state byte at (IX+$12).')
    print()
    print('  $B48F BIT 7,(IY+$4D) = $84CC: set by $91D4 once a pack has loaded, so')
    print('  the REWIND TAPE prompt appears only on a cold boot.  $84CC = $%02X here.'
          % m[0x84CC])


# ---------------------------------------------------------------- restart
def cmd_restart():
    h = boot(); m = h.memobj.m
    die(h, score=(0x03, 0x45, 0x67))
    snap = bytes(m)
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    for tgt, lbl in [(0xB35A, '$B3C6 -> $B35A  new game'),
                     (0x8767, '  -> $8767      draw the table'),
                     (0xB47B, '  -> $B47B      attract poll')]:
        pc, dt, n = run_until_pc(h, tgt, limit=60_000_000)
        print('  %-32s %8.2f frames' % (lbl, dt / FRAME_T))
    press(h, P1['fire'])
    for tgt, lbl in [(0xB3D0, '  FIRE -> $B3D0 level setup'),
                     (LOOP_TOP, '  -> $8503       LIVE AGAIN (tape reload)')]:
        pc, dt, n = run_until_pc(h, tgt, limit=80_000_000)
        print('  %-32s %8.2f frames  (%.2f s)' % (lbl, dt / FRAME_T, dt / FRAME_T / 50.08))
    h.ports.release_all()
    now = bytes(m)
    print('  p1 block %s' % ' '.join('%02X' % b for b in m[0x8420:0x8430]))
    print('  score %s, health %02X%02X, keys %d, dungeon %d'
          % (' '.join('%02X' % b for b in m[0x8424:0x8427]), m[0x8422], m[0x8423],
             m[0x8428], m[0x8403]))
    for nm, lo, hi in [('map $8000-$83FF', 0x8000, 0x8400),
                       ('actors $5C00-$5F00', 0x5C00, 0x5F00),
                       ('tables $8826-$8916', 0x8826, 0x8916)]:
        print('  %-22s %d bytes differ from the moment of death'
              % (nm, sum(1 for a in range(lo, hi) if snap[a] != now[a])))
    print('  the map comes back from TAPE, not from a saved copy: $B3D0 -> $9175.')


# ---------------------------------------------------------------- rejoin
def cmd_rejoin():
    h = boot(); m = h.memobj.m
    run_until_pc(h, LOOP_TOP)
    press(h, P2['fire'])
    run_until_pc(h, LOOP_TOP); run_until_pc(h, LOOP_TOP)
    h.ports.release_all(); run_until_pc(h, LOOP_TOP)
    print('  player 2 joined with his own FIRE: $8454=%02X $844B=%02X health %02X%02X'
          % (m[0x8454], m[0x844B], m[0x8442], m[0x8443]))
    h.poke(0x8424, 0x00, 0x12, 0x34); h.poke(0x8428, 3); h.poke(0x8429, 2)
    h.poke(0x8434, 0x22); h.poke(0x8433, 0x18)
    h.poke(0x8422, 0x00, 0x00)
    print('  p1 dies with score 001234, 3 keys, 2 potions, inventory $22:')
    for i in range(4):
        pc, dt, n = run_until_pc(h, (LOOP_TOP, 0x855C))
        print('     pass %d ctr=%d $842B=%02X $842E=%02X name=%-4s  %.2f frames %s'
              % (i + 1, m[0x8491], m[0x842B], m[0x842E], pname(m), dt / FRAME_T,
                 'MAIN LOOP RETURNED' if pc == 0x855C else ''))
        if pc == 0x855C:
            break
    print('  the loop does NOT end -- p2 is alive, so $94AE never sets $847D bit 7,')
    print('  and the name entry runs from $8509 CALL $A38A -> $9432, once per pass.')
    press(h, P1['shift'])
    for _ in range(3):
        run_until_pc(h, (LOOP_TOP, 0x855C))
    h.ports.release_all()
    print('  SHIFT commits: $842E=%02X, record %s, $84C6=%d'
          % (m[0x842E], ' '.join('%02X' % b for b in m[PEND:PEND + 8]), m[0x84C6]))
    before = bytes(m)
    press(h, P1['fire'])
    costs = []
    for i in range(10):
        pc, dt, n = run_until_pc(h, (LOOP_TOP, 0x855C))
        costs.append('%.2f' % (dt / FRAME_T))
        if not (m[0x8434] & 0x80) and i > 1:
            break
    h.ports.release_all()
    after = bytes(m)
    print('  FIRE rejoins.  pass costs %s frames -- no stall.' % ' '.join(costs))
    print('  p1 block %s' % ' '.join('%02X' % b for b in m[0x8420:0x8430]))
    for nm, lo, hi in [('map', 0x8000, 0x8400), ('actors', 0x5C00, 0x5F00)]:
        ch = [a for a in range(lo, hi) if before[a] != after[a]]
        print('  %-7s %d bytes changed  %s'
              % (nm, len(ch), ' '.join('$%04X' % a for a in ch[:10])))
    print('  the dropped item and the whole map SURVIVE the rejoin; only the player')
    print('  block is reset.  He comes back at $9689\'s placement, health BCD 2000,')
    print('  score/keys/potions/inventory ZERO -- so a rejoin is strictly a loss.')


# ---------------------------------------------------------------- block
def cmd_block():
    h = boot(); m = h.memobj.m
    run_until_pc(h, LOOP_TOP)
    h.poke(0x8433, 0x18)
    h.poke(0x8422, 0x00, 0x00)
    for tgt, lbl in [(0x9431, 'the death routine $93D9..$9431 (incl. sound $0F)'),
                     (LOOP_TOP, 'the rest of the death pass'),
                     (0x855C, 'the pass that sets levelDone'),
                     (OVER_TOP, 'the $B39B spin')]:
        pc, dt, n = run_until_pc(h, tgt, limit=20_000_000)
        print('  %-46s %8.2f frames %7d instr' % (lbl, dt / FRAME_T, n))
    print('  -> the death itself does NOT stall.  Sound $0F costs 0.01 frames in')
    print('     $BA2B; it is a QUEUE write, not the 103.8-frame pickup stall.')
    print()
    print('  the only stall in the whole path is the REWIND TAPE prompt:')
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    run_until_pc(h, 0xB47B)
    h.poke(0x84CC, 0x00)               # force the cold-boot arm
    press(h, P1['fire'])
    run_until_pc(h, 0xB48F, limit=60_000_000)
    h.ports.release_all()
    for tgt, lbl in [(0xB49E, '  string 1 ($8A08, centred, into the shadow)'),
                     (0xB4A4, '  string 2'),
                     (0xB4AA, '  string 3'),
                     (0xB4AD, '  $8B8B: attrs $47, $847D bit 2, $9CD7 blit')]:
        pc, dt, n = run_until_pc(h, tgt, limit=60_000_000)
        print('  %-46s %8.2f frames %7d instr' % (lbl, dt / FRAME_T, n))
    print('  the 102.8 frames are HALT-paced, not CPU-bound (628 instr/frame):')
    print('  $8B98 SET 2,(IY-2) arms $9D01, which $9CD7 FALLS INTO, and $9D22')
    print('  plays sound $10 and then spins B=$32 times over CALL $9432 / HALT /')
    print('  HALT = 100 video frames.  ($9D1E takes B=$82 = 260 frames when $847D')
    print('  bit 5 is set.)  This is the same family as the 103.8-frame pickup.')


# ---------------------------------------------------------------- lives
SKIP = [(0x4000, 0x5B00), (0xC000, 0x10000)]


def cmd_lives():
    print("""  THE MODEL.  There is no life counter and no continue.  Health IS the life:
  $93CD kills at packed-BCD $0000, $9440 offers the dead player a REJOIN for
  FIRE, and $946E charges him his whole score, keys, potions and inventory for
  it.  In a one-player game the rejoin is unreachable (the loop has already
  ended), so one death = game over = name entry = a brand-new game.

  The claim is negative, so here is the exhaustive form: the full 64K diff over
  the death pass, display file and shadow screen excluded.""")
    h = boot(); m = h.memobj.m
    run_until_pc(h, LOOP_TOP)
    h.poke(0x8433, 0x18)
    before = bytes(m)
    h.poke(0x8422, 0x00, 0x00)
    run_until_pc(h, LOOP_TOP)
    after = bytes(m)
    ch = [a for a in range(0x10000)
          if not any(lo <= a < hi for lo, hi in SKIP) and before[a] != after[a]]
    why = {0x8043: 'the dropped item', 0x8422: 'health', 0x8423: 'health',
           0x842B: '(IX+11) |= $C3', 0x842E: '(IX+14) = $80, arms the name entry',
           0x8434: 'the DEAD bit', 0x847E: 'display flags', 0x8491: 'the pass counter',
           0x8497: 'the video-frame counter', 0x8499: 'the animation countdown',
           0xA9B9: 'a self-modified operand', 0xBDB3: 'the sound queue',
           0xBDB4: 'the sound queue', 0xBDB5: 'the sound queue',
           0xBDB6: 'the sound queue', 0xBDC0: 'the sound queue',
           0xBDC1: 'the sound queue'}
    print('  %d bytes changed:' % len(ch))
    for a in ch:
        print('     $%04X  %02X -> %02X   %s' % (a, before[a], after[a], why.get(a, '?')))
    print('  not one of them is a counter that a second death would decrement again.')
    print('  $84C6 is a PENDING-RECORD count (0..2), bumped by $93B5 when the name')
    print('  entry is armed and zeroed by $872F when the sort consumes it.')


CMDS = {'exits': cmd_exits, 'loop': cmd_loop, 'name': cmd_name, 'cursor': cmd_cursor,
        'hiscore': cmd_hiscore, 'attract': cmd_attract, 'restart': cmd_restart,
        'rejoin': cmd_rejoin, 'block': cmd_block, 'lives': cmd_lives}


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(CMDS) if what == 'all' else [what]
    for nm in names:
        print('=== %s ===' % nm)
        CMDS[nm]()
        print()


if __name__ == '__main__':
    main()
