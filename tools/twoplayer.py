#!/usr/bin/env python3
# WARNING, added after the fact: cmd_formula's gate is a TAUTOLOGY.  It hooks
# $A4AF and reads A out of the machine, but A already contains the phase term
# the formula is supposed to produce, so the test only exercises "+ (IX+15)"
# and the $7B00 lookup and cannot detect a wrong phase rule.  The honest gate
# is in tools/playersprite.py: it predicts the id from (IX+13),(IX+14),(IX+15)
# read at $A496 BEFORE the code adds anything and compares it with the pointer
# the blitter actually loads.  The rule itself is
#     id = (IX+15) + (IX+13) + (bit6 ? 8 : 0) + (bit6 && bit7 ? 8 : 0)
# -- $A4A7's ADD A,8 is NESTED INSIDE $A49F's, so bits 7:6 = 00/01/10/11 give
# +0/+8/+0/+16 and the walk cycle is A,B,A,C.  Do not trust cmd_formula's
# 80/80 on its own.
"""
twoplayer.py -- drive the REAL Z80 with BOTH players active and measure the
two-player HUD, the two sprite sets, the draw order and the off-screen cull.

Nothing here is inferred from the artwork.  Every number printed is either read
out of live RAM after driving the original, or logged from a PC hook inside the
routine that produced it.

    python tools/twoplayer.py join            how player 2 joins, pass by pass
    python tools/twoplayer.py hud             OCR rows 20-23 with both playing
    python tools/twoplayer.py sprites         the two banks, ids and inks
    python tools/twoplayer.py order [N]       blit order, logged at $9DD2
    python tools/twoplayer.py formula [N]     GATE: (IX+15)+(IX+13)+8*phase
    python tools/twoplayer.py overlap         drive them onto the same cell
    python tools/twoplayer.py offscreen       sweep p2 out of the viewport
    python tools/twoplayer.py panel           who writes the panel attributes
    python tools/twoplayer.py shot [file]     PNG: elf + warrior, both live
    python tools/twoplayer.py pair [p1] [p2]  PNG of any character pairing

THE JOIN, $9440 (reached from $9432, called at $A38A inside the main loop)

    BIT 7,(IX+$14) / RET z          bit 7 = NOT PLAYING; a live player returns
    LD A,(IX+14) / OR A / JP nz     ...
    BIT 4,(IX+7) / RET z            (IX+7) bit 4 is FIRE
    RES 7,(IX+$14)                  <-- the join
    CALL $9689                      place him, point his 8 sprite ids at his
                                    own bank ($9595), fill his damage table
    JR nc,$946E                     ok -> zero score/keys/potions, health $2000
    LD A,($8403) / DEC A / JR z     dungeon 1 joins anyway
    SET 7,(IX+$14)                  otherwise refused, sound 13
    $948C  A=IXL & $20 -> DE'=$5080 (p1) or $5091 (p2), JP $B5E8 = repaint

THE SAMPLER is $8503, the main-loop top, exactly as tools/sim_move.py says.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC, FRAME_T   # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402
from fixchar import fix, NAME                                    # noqa: E402
import screen                                                    # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503
P1, P2 = 0x8420, 0x8440
FONT = 0x77B0

# $862E (player 1) and $8657 (player 2), the control method ($FFFC/$FFFB = $2A
# in the captured state, so both fall through to the "else" arm).
P1KEYS = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D', 'fire': 'Z'}
P2KEYS = {'up': '8', 'down': 'I', 'left': 'K', 'right': 'L', 'fire': 'M'}


# --------------------------------------------------------------- the stepper --
def step_pass(h, hooks=None):
    """Run to the next $8503.  hooks: {pc: fn(h)} called BEFORE that opcode."""
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return regs[T] - t0
        if hooks and pc in hooks:
            hooks[pc](h)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


def boot(p1=3, p2=0, state=STATE):
    """Load the captured level-1 state and repair BOTH sprite banks."""
    st = pickle.load(open(state, 'rb'))
    mem = bytearray(st[0])
    fix(mem, p1, p2)
    h = Harness()
    h.load_state((bytes(mem), st[1], st[2]))
    step_pass(h)                       # align on the loop top before sampling
    return h


def press(h, *names):
    h.ports.release_all()
    for n in names:
        sel, bit = KM[n]
        h.ports.press(sel, keymask(bit))


def join(h, passes=8, verbose=False):
    """Hold player 2's FIRE until $8454 bit 7 clears.  Returns the pass count."""
    press(h, P2KEYS['fire'])
    m = h.memobj.m
    for i in range(passes):
        step_pass(h)
        if verbose:
            print(f'  pass {i+1}: $8447=${m[0x8447]:02X} $8454=${m[0x8454]:02X} '
                  f'$844B=${m[0x844B]:02X} pos=({m[0x8440]},{m[0x8441]}) '
                  f'hp=${m[0x8442]:02X}{m[0x8443]:02X}')
        if not m[0x8454] & 0x80:
            h.ports.release_all()
            return i + 1
    h.ports.release_all()
    return None


# ------------------------------------------------------------------- the HUD --
def rowcol(addr):
    o = addr - 0x4000
    return ((o >> 11) * 8 + ((o >> 5) & 7), o & 31)


def ocr(m, rows=range(20, 24), width=32):
    """Read the HUD back off the REAL screen through the game's own font.

    The font is NOT 96 glyphs: $7E29's VALKYRIE is codes $81..$89, so the
    table runs at least to $89.  Scan the whole range the panels can reach.
    """
    glyphs = {}
    for code in range(32, 0x90):
        g = bytes(m[FONT + (code - 32) * 8: FONT + (code - 32) * 8 + 8])
        glyphs.setdefault(g, code)
    out = []
    for row in rows:
        line = ''
        for col in range(width):
            o = row * 32 + col
            addr = 0x4000 + ((o & 0x300) << 3) + (o & 0xFF)
            cell = bytes(m[addr + 256 * pr] for pr in range(8))
            code = glyphs.get(cell)
            if code is None:
                line += '?'
            elif code == 32:
                line += ' '
            elif 33 <= code < 127:
                line += chr(code)
            else:
                line += '·'          # a glyph outside ASCII: panel art
        attrs = [m[0x5800 + row * 32 + c] for c in range(width)]
        out.append((row, line, attrs))
    return out


def show_hud(m, label=''):
    print(f'  rows 20-23 of the REAL display{label}')
    for row, line, attrs in ocr(m):
        print(f'   {row} |{line}|')
        print(f'      ' + ''.join(f'{a:02X}' for a in attrs))


# ----------------------------------------------------------------- the blits --
def blit_log(h, passes=1, extra=None):
    """Log every 16x16 blit: (dest, source, attr) at the moment $9DD2 runs.

    $A225 CALL $B557 / EX DE,HL puts the destination in DE and $A23E takes the
    record's attribute into C and the bitmap pointer into SP, so at $9DD2:
    HL = destination, SP = record+1, C = the record's attribute byte.
    """
    log = []
    regs = h.sim.registers
    m = h.memobj.m

    def at_blit(hh):
        sp = regs[12]
        hl = (regs[6] << 8) | regs[7]
        c = regs[3]
        log.append({'dest': hl, 'src': sp - 1, 'attr': c,
                    'p1': (m[P1], m[P1 + 1]), 'p2': (m[P2], m[P2 + 1]),
                    'cam': (m[0x848B], m[0x848C])})
    hooks = {0x9DD2: at_blit}
    if extra:
        hooks.update(extra)
    for _ in range(passes):
        step_pass(h, hooks)
    return log


def id_of(m, ptr):
    """Reverse the $7B00 pointer table: which sprite id points at this record?"""
    for i in range(1, 256):
        a = 0x7B00 + (i - 1) * 2
        if (m[a] | (m[a + 1] << 8)) == ptr:
            return i
    return None


def which(ptr):
    if 0x5F00 <= ptr < 0x6320:
        return f'P1 bank rec{(ptr - 0x5F00) // 33}'
    if 0x6320 <= ptr < 0x6740:
        return f'P2 bank rec{(ptr - 0x6320) // 33}'
    # $9689 -> $9579 points the joining player's first six ids at $F45E while
    # he MATERIALISES; $A4C8 puts them back on his own bank six passes later.
    if 0xF45E <= ptr < 0xF524:
        return f'MATERIALISE rec{(ptr - 0xF45E) // 33}'
    return ''


# -------------------------------------------------------------- the commands --
def cmd_join():
    h = boot()
    m = h.memobj.m
    print('=== player 2 joins by pressing FIRE (M) ===')
    print(f'before: $8454=${m[0x8454]:02X} (bit7 = NOT PLAYING), '
          f'$844B=${m[0x844B]:02X}, $844F=${m[0x844F]:02X} (sprite base), '
          f'pos=({m[P2]},{m[P2+1]})')
    n = join(h, verbose=True)
    print(f'joined after {n} passes with M held')
    print(f'after:  $8454=${m[0x8454]:02X}, $844B=${m[0x844B]:02X}, '
          f'health=${m[0x8442]:02X}{m[0x8443]:02X}, pos=({m[P2]},{m[P2+1]}), '
          f'p1 pos=({m[P1]},{m[P1+1]})')
    print(f'p2 name index +$13 = ${m[0x8453]:02X}, damage sel +$15 = '
          f'${m[0x8455]:02X}, sprite base +$0F = {m[0x844F]}')
    show_hud(m, ' after the join')


def cmd_hud():
    for p1, p2 in ((3, 0), (1, 2)):
        h = boot(p1, p2)
        m = h.memobj.m
        join(h)
        for _ in range(3):
            step_pass(h)
        print(f'=== p1={NAME[p1]}  p2={NAME[p2]} ===')
        show_hud(m)
        print()


def cmd_sprites():
    h = boot()
    m = h.memobj.m
    join(h)
    for _ in range(6):
        step_pass(h)
    print('=== the two sprite sets ===')
    for n, base in ((1, P1), (2, P2)):
        b = m[base + 0x0F]
        print(f'player {n}: (IX+15) = {b} (${b:02X}); table entries '
              f'${0x7B00 + (b - 1) * 2:04X}..')
        for slot in range(8):
            row = []
            for ph in range(3):
                sid = (b + slot + 8 * ph) & 0xFF
                a = 0x7B00 + (sid - 1) * 2
                p = m[a] | (m[a + 1] << 8)
                row.append(f'id {sid:3} -> ${p:04X} {which(p)}')
            print(f'   slot {slot}: ' + ' | '.join(row))
        recs = {}
        for sid in range(b, b + 24):
            a = 0x7B00 + ((sid & 0xFF) - 1) * 2
            p = m[a] | (m[a + 1] << 8)
            recs[m[p]] = recs.get(m[p], 0) + 1
        print(f'   record attribute bytes over the 24 walk records: '
              + ', '.join(f'${k:02X} x{v}' for k, v in sorted(recs.items())))


def cmd_order(passes=6):
    h = boot()
    m = h.memobj.m
    join(h)
    press(h, P1KEYS['right'], P2KEYS['down'])
    print('=== blit order, logged at $9DD2 (p1 holds right, p2 holds down) ===')
    for p in range(passes):
        log = blit_log(h, 1)
        print(f'pass {p+1}: cam={log[0]["cam"] if log else "?"} '
              f'p1={log[0]["p1"] if log else "?"} p2={log[0]["p2"] if log else "?"}')
        for i, b in enumerate(log):
            tag = which(b['src'])
            if tag:
                r, c = rowcol(b['dest'] - 0x8000)   # $C000 shadow -> $4000 map
                print(f'   #{i:3} dest ${b["dest"]:04X} (row {r} col {c})  '
                      f'src ${b["src"]:04X} {tag}  attr ${b["attr"]:02X}  '
                      f'id {id_of(m, b["src"])}')
    h.ports.release_all()


def cmd_overlap():
    h = boot()
    m = h.memobj.m
    join(h)
    for _ in range(8):          # past the 5-pass materialise
        step_pass(h)
    print('=== overlap: player 2 poked onto player 1 ===')
    print(f'p1 ({m[P1]},{m[P1+1]})  p2 ({m[P2]},{m[P2+1]})')
    m[P2], m[P2 + 1] = m[P1], m[P1 + 1]
    log = blit_log(h, 1)
    for i, b in enumerate(log):
        tag = which(b['src'])
        if tag:
            print(f'   #{i:3} dest ${b["dest"]:04X} src ${b["src"]:04X} {tag} '
                  f'attr ${b["attr"]:02X}')
    print(f'p1 ({m[P1]},{m[P1+1]})  p2 ({m[P2]},{m[P2+1]}) after the pass')
    print(f'(IX+14) p1 ${m[P1+14]:02X}  p2 ${m[P2+14]:02X}   '
          f'($AAC4 sets bit 3 on player-vs-player contact)')
    return h


def draw_trace(h, passes=1):
    """One pass, with every player draw attributed to the player who asked.

    $A4AF ADD A,(IX+15) / JP $A1DA is the last instruction that still knows
    WHICH player is being drawn, so hook there; then $9DD2 is the full 16x16
    blit and $A24C is the clipped dispatch ($9E4B / $9EA0 / $9ED7).
    """
    regs = h.sim.registers
    m = h.memobj.m
    out = []
    cur = {}

    def at_pick(hh):
        ix = (regs[8] << 8) | regs[9]
        cur.clear()
        cur.update(player=1 if ix == P1 else 2, x=m[ix], y=m[ix + 1],
                   camx=m[0x848B], camy=m[0x848C], drawn=None)
        out.append(cur.copy())

    def at_full(hh):
        if out:
            out[-1]['drawn'] = 'full'
            out[-1]['dest'] = (regs[6] << 8) | regs[7]
            out[-1]['src'] = regs[12] - 1
            out[-1]['attr'] = regs[3]

    def at_clip(hh):
        if out:
            out[-1]['drawn'] = 'clipped'
            out[-1]['edge'] = regs[4]

    for _ in range(passes):
        step_pass(h, {0xA4AF: at_pick, 0x9DD2: at_full, 0xA24C: at_clip})
    return out


def cmd_offscreen():
    h = boot()
    m = h.memobj.m
    join(h)
    for _ in range(8):                 # past the materialise
        step_pass(h)
    print('=== the off-screen cull and the edge clip, $A1DA ===')
    print(' A = (coord + 3 - cam) & $7F.  CP $44 / RET nc culls; CP 3 sets the')
    print(' near-edge flag; SUB $43 (x) / SUB $2B (y) / RET nc culls the far')
    print(' side; ADD A,3 with carry sets the far-edge flag.  Player 2 is poked')
    print(' to the coordinate and the pass is traced at $A4AF/$9DD2/$A24C.')
    st0 = h.save_state()
    for axis, name, lim in ((0, 'x', 128), (1, 'y', 128)):
        print(f'\n  {name}   A     result            dest    edge')
        prev = None
        for v in range(0, lim, 2):
            h.load_state(st0)
            m[P2 + axis] = v
            tr = [t for t in draw_trace(h, 1) if t['player'] == 2]
            if not tr:
                continue
            t = tr[0]
            cam = t['camx'] if axis == 0 else t['camy']
            coord = t['x'] if axis == 0 else t['y']
            a = (coord + 3 - cam) & 0x7F
            res = t['drawn'] or 'NOT DRAWN'
            line = (f'  {coord:3}  ${a:02X}   {res:16}  '
                    f'{"$%04X" % t["dest"] if t.get("dest") else "":7} '
                    f'{"$%X" % t["edge"] if t.get("edge") is not None else ""}')
            if res != prev:                       # print every transition...
                print('  ' + '-' * 46)
            prev = res
            print(line)
    h.load_state(st0)


def cmd_shot(out=None, p1=3, p2=0, walk=9):
    from PIL import Image
    h = boot(p1, p2)
    m = h.memobj.m
    n = join(h)
    print(f'p2 joined after {n} passes')
    for _ in range(6):                 # let him finish materialising
        step_pass(h)
    # walk them apart, both at once, and stop ON a walk frame so the two
    # sprites are in different poses in the picture
    press(h, P1KEYS['right'], P2KEYS['down'])
    for _ in range(walk):
        step_pass(h)
    print(f'p1 ({m[P1]},{m[P1+1]}) {NAME[p1]}   p2 ({m[P2]},{m[P2+1]}) {NAME[p2]}'
          f'   cam ({m[0x848B]},{m[0x848C]})')
    show_hud(m)
    out = out or os.path.join(ROOT, 'build', 'twoplayer.png')
    img = screen.render(m, 0x4000, 0x5800)
    img = img.resize((img.width * 3, img.height * 3), Image.NEAREST)
    img.save(out)
    print(f'wrote {out}')
    return h


def cmd_panel():
    """Who writes the panel ATTRIBUTES, and the low-health flash.

    $8534 CALL $9788 runs every pass and patches the ISR's own operands:
        $9788 BIT 7,(IY-$4B)=$8434 / LD A,($8433) / LD B,(IY-$5D)=$8422
              LD HL,$A2CE / CALL $97A8            player 1
        $979A BIT 7,(IY-$2B)=$8454 / LD A,($8453) / LD B,(IY-$3D)=$8442
              LD HL,$A2D6                          player 2
        $97A8 index 0/8/16/else -> E = $42/$45/$46/$44
              LD A,B / CP 2 / JR nc                health high BCD byte
              BIT 4,(IY+$18)=$8497 / LD E,D=$40    ...else FLASH bright black
    and the ISR at $A2CA/$A2D2 paints with them EVERY video frame.
    """
    h = boot(3, 0)
    m = h.memobj.m
    join(h)
    for _ in range(4):
        step_pass(h)
    print('=== the panel attributes are written by the ISR, every frame ===')
    h.memobj.watch(0x5A80, 0x5B00)
    step_pass(h)
    h.memobj.unwatch()
    byp = {}
    for pc, a, v in h.memobj.log:
        byp.setdefault((pc, v), []).append(a - 0x5800)
    for (pc, v), cells in sorted(byp.items()):
        rows = sorted({c // 32 for c in cells})
        cols = sorted({c % 32 for c in cells})
        print(f'  PC ${pc:04X} writes ${v:02X} x{len(cells):4}  rows {rows} '
              f'cols {min(cols)}..{max(cols)}')
    print('\n=== the low-health flash: health high byte < BCD 02 ===')
    for hp in (0x2000, 0x0200, 0x0199, 0x0100, 0x0050):
        m[0x8422], m[0x8423] = hp >> 8, hp & 0xFF
        seen = {}
        for _ in range(12):
            step_pass(h)
            seen[m[0xA2CE]] = seen.get(m[0xA2CE], 0) + 1
        print(f'  health ${hp:04X} -> $A2CE over 12 passes: '
              + ', '.join(f'${k:02X} x{v}' for k, v in sorted(seen.items())))


def cmd_pair(p1=1, p2=2, out=None):
    """A shot of any character pairing.

    The captured state's panel was painted at level start from the boot bug's
    garbage index, so player 1's NAME line is stale whatever $8433 says.  Put
    him back on the join path -- the game's own $9440, exactly what a player
    who has just died does -- so $B5E8 repaints his panel from the repaired
    index.  Only the two bytes the join path itself tests are poked.
    """
    from PIL import Image
    h = boot(p1, p2)
    m = h.memobj.m
    m[0x8434] |= 0x80                  # player 1: NOT PLAYING
    m[0x842E] = 0                      # ...and not mid-animation
    press(h, P1KEYS['fire'], P2KEYS['fire'])
    for _ in range(12):
        step_pass(h)
    h.ports.release_all()
    press(h, P1KEYS['right'], P2KEYS['down'])
    for _ in range(9):
        step_pass(h)
    print(f'p1 {NAME[p1]} ({m[P1]},{m[P1+1]})  p2 {NAME[p2]} ({m[P2]},{m[P2+1]})')
    print(f'panel inks: $A2CE=${m[0xA2CE]:02X}  $A2D6=${m[0xA2D6]:02X}')
    show_hud(m)
    out = out or os.path.join(ROOT, 'build', f'twoplayer_{p1}{p2}.png')
    img = screen.render(m, 0x4000, 0x5800)
    img.resize((img.width * 3, img.height * 3), Image.NEAREST).save(out)
    print(f'wrote {out}')


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else 'join'
    if cmd == 'join':
        cmd_join()
    elif cmd == 'hud':
        cmd_hud()
    elif cmd == 'sprites':
        cmd_sprites()
    elif cmd == 'order':
        cmd_order(int(args[1]) if len(args) > 1 else 6)
    elif cmd == 'overlap':
        cmd_overlap()
    elif cmd == 'offscreen':
        cmd_offscreen()
    elif cmd == 'formula':
        cmd_formula(int(args[1]) if len(args)>1 else 40)
    elif cmd == 'pair':
        cmd_pair(int(args[1]) if len(args)>1 else 1,
                 int(args[2]) if len(args)>2 else 2)
    elif cmd == 'panel':
        cmd_panel()
    elif cmd == 'shot':
        cmd_shot(args[1] if len(args) > 1 else None)
    else:
        print(__doc__)


def cmd_formula(passes=40):
    """Gate: the record the game blits == $7B00[(IX+15)+(IX+13)+8*phase].

    Hook $A4AF, where A already holds (IX+13)+8*phase and IX still names the
    player, work the id and the pointer out of live RAM, then compare with the
    source pointer the blitter actually loads at $9DD2.  Both players, driven
    in all four directions.
    """
    h = boot(3, 0)
    m = h.memobj.m
    join(h)
    for _ in range(6):
        step_pass(h)
    regs = h.sim.registers
    checks = {1: [0, 0], 2: [0, 0]}
    pend = {}

    def at_pick(hh):
        ix = (regs[8] << 8) | regs[9]
        a = (regs[0] + m[ix + 0x0F]) & 0xFF
        t = 0x7B00 + ((a - 1) & 0xFF) * 2
        pend.clear()
        pend.update(player=1 if ix == P1 else 2, sid=a,
                    ptr=m[t] | (m[t + 1] << 8), slot=m[ix + 0x0D],
                    phase=m[ix + 0x0E] >> 6)

    def at_full(hh):
        if not pend:
            return
        src = regs[12] - 1
        c = checks[pend['player']]
        c[0] += 1
        c[1] += (src == pend['ptr'])
        pend.clear()

    hooks = {0xA4AF: at_pick, 0x9DD2: at_full}
    for keys in (('right', 'left'), ('down', 'up'), ('up', 'right'),
                 ('left', 'down')):
        press(h, P1KEYS[keys[0]], P2KEYS[keys[1]])
        for _ in range(passes // 4):
            step_pass(h, hooks)
    h.ports.release_all()
    for p in (1, 2):
        n, ok = checks[p]
        print(f'  player {p}: (IX+15)+(IX+13)+8*phase named the blitted record '
              f'{ok}/{n} draws')


if __name__ == '__main__':
    main()
