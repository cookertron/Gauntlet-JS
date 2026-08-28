#!/usr/bin/env python3
"""
hstable.py -- the RANKED SCORE TABLE at $8826 and its attract display.

    python tools/hstable.py all
    python tools/hstable.py layout    base, extent, entry format, PROVENANCE
    python tools/hstable.py writers   write-watch: who ever touches $8826..$8915
    python tools/hstable.py commit    FULL commits through the name-entry path
    python tools/hstable.py rank      the insertion rule, enumerated
    python tools/hstable.py millions  the 6-bit millions field and its 4-bit display
    python tools/hstable.py persist   the table across game over -> new game
    python tools/hstable.py attract   the four pages, their cadence, and PNGs

Everything here drives the REAL Z80 through tools/harness.py.  Nothing loads
the engine.

THE SHAPE, read off the machine at $869F..$8825 and then measured:

    $8826  4 x 60 bytes   the ranked tables, ONE PER CHARACTER, 10 x 6 bytes
    $8916  6 bytes        the staging key $869F builds before it sorts
    $891C  .. code        -- and that is why a bad character tag is harmless

    $869F  consume ($84C6) pending records at $7F2B, 8 bytes each
    $8757  pack a name letter + 2 bits of the millions counter into one byte
    $8818  A=$F8, B=tag/8+1 -> IX = table base, A = the tag again
    $86ED  the INSERTION SORT: 10 slots, first slot whose key is strictly
           LESS than the new one, everything below it shifts down one, the
           tenth falls off the end
    $8767  draw one page (the next character in the rotation) into the shadow
    $87AB  draw one row;  $B6C0  the game's own $77B0 font, glyph=(code-32)*8
    $B470  the attract loop: $869F, then poll until $8497 wraps -- 256 frames
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, FRAME_T, TAPE_CALL_PC, SP   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402
import screen                                                        # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
BUILD = os.path.join(ROOT, 'build')
LOOP_TOP = 0x8503
OVER_TOP = 0xB3B9
SORT = 0x869F
DRAW = 0x8767
BLIT = 0x87A8
TABLE = 0x8826
STAGE = 0x8916
ENDT = 0x891C
PEND = 0x7F2B
COUNT = 0x84C6
CURCOL = 0x8439
NAMEBUF = PEND + 3
P1 = dict(up='1', down='Q', left='S', right='D', fire='Z', shift='CAPS')
CHARNAME = {0: 'WARRIOR', 1: 'VALKYRIE', 2: 'WIZARD', 3: 'ELF'}
BLOCK_C_DEST = 0x8400


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


def spin_frames(h, nframes, stop=(0xB3C6,)):
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
    press(h, P1[key]); spin_frames(h, 8); h.ports.release_all(); spin_frames(h, 2)


def die(h, tag=0x18, score=(0x00, 0x00, 0x00), millions=None):
    """Kill player 1 and stop at the top of the post-death loop."""
    run_until_pc(h, LOOP_TOP)
    h.poke(0x8433, tag)
    h.poke(0x8424, *score)
    if millions is not None:
        h.poke(0x84C8, millions)
    h.poke(0x8422, 0x00, 0x00)
    run_until_pc(h, 0x855C)
    run_until_pc(h, OVER_TOP)


# ------------------------------------------------------------------ decoding
def unpack(ent):
    """the 6-byte entry -> (millions, name, score-string)"""
    mm, nm = 0, ''
    for i in range(3):
        b = ent[i]
        mm = (mm << 2) | (b >> 6)
        v = b & 0x3F
        nm += ' ' if v == 0 else chr(0x40 + v)
    return mm, nm, '%02X%02X%02X' % (ent[3], ent[4], ent[5])


def pack(name, score, millions=0):
    """the inverse -- what $8757 would build."""
    out = []
    c = (millions << 2) & 0xFF
    for i in range(3):
        ch = name[i] if i < len(name) else ' '
        v = 0 if ch == ' ' else (ord(ch) - 0x40) & 0x3F
        out.append(((c >> 6) << 6) | v)
        c = (c << 2) & 0xFF
    return bytes(out) + bytes(score)


def base_of(c):
    return TABLE + 0x3C * c


def rows(m, c):
    b = base_of(c)
    return [unpack(m[b + 6 * k: b + 6 * k + 6]) for k in range(10)]


def fmt_row(r):
    mm, nm, sc = r
    return '%s %s%s' % (nm, ('%d' % mm) if mm else ' ', sc)


def show(m, c, tag=''):
    print('     %-8s %-6s %s' % (CHARNAME[c], tag,
                                 ' | '.join(fmt_row(r) for r in rows(m, c))))


def bcd(s):
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ------------------------------------------------------------------ layout
def cmd_layout():
    h = boot(); m = h.memobj.m
    img = open(os.path.join(BUILD, 'image.bin'), 'rb').read()
    imga = open(os.path.join(BUILD, 'image_a.bin'), 'rb').read()
    print('  BASE $8826, EXTENT $8826..$8915 -- FOUR 60-byte tables, one per')
    print('  character, ten 6-byte entries each.  $8916..$891B is the 6-byte')
    print('  STAGING key; $891C is live code (BIT 6,(IY-1)).')
    print('  $8818: LD IX,$87EA / DE=$3C / B times {IX+=$3C; A+=8} with')
    print('  B = tag/8 + 1 -> IX = $8826 + $3C*index and A = the tag.')
    for c in range(4):
        print('     %-8s index %d  tag $%02X  $%04X..$%04X'
              % (CHARNAME[c], c, c * 8, base_of(c), base_of(c) + 0x3B))
    print()
    print('  PROVENANCE.  $8826 is block C offset $%04X (block C loads at $8400,'
          % (TABLE - BLOCK_C_DEST))
    print('  length $7777).  The bytes come off the TAPE and nothing writes them')
    print('  at boot -- see "writers".  Block A cannot be the source: it occupies')
    print('  $8600..$D80F and block C is loaded ON TOP of it afterwards.')
    from harness import tape_blocks, SIDE1
    blocks = {f: p for f, p in tape_blocks(SIDE1)}
    tape_c = blocks[0x82]
    off = TABLE - BLOCK_C_DEST
    same_img = tape_c[off:off + 0xF6] == img[TABLE:TABLE + 0xF6]
    same_live = tape_c[off:off + 0xF6] == bytes(m[TABLE:TABLE + 0xF6])
    print('     tape block $82[$%04X..] == build/image.bin[$8826..]  %s'
          % (off, same_img))
    print('     tape block $82[$%04X..] == live RAM at $8826         %s'
          % (off, same_live))
    print('     block A\'s own bytes at $8826 (overwritten by C): %s'
          % ' '.join('%02X' % b for b in imga[TABLE:TABLE + 6]))
    print()
    print('  THE ENTRY, 6 bytes.  $8757 builds it and $87AB reads it back:')
    print('     +0..+2  b7:b6 = two bits of the MILLIONS counter, MSB first')
    print('             b5:b0 = the letter, $01..$1A = A..Z, $00 = SPACE')
    print('     +3..+5  the packed-BCD score, HIGH byte first -- verbatim')
    print('  so the millions field is 6 bits spread across the three name bytes,')
    print('  and the score is the same 3-byte packed BCD the game plays with.')
    print()
    print('  THE SHIPPED DEFAULTS -- every slot of every table identical:')
    for c in range(4):
        b = base_of(c)
        mm, nm, sc = unpack(m[b:b + 6])
        allsame = all(bytes(m[b + 6 * k:b + 6 * k + 6]) == bytes(m[b:b + 6])
                      for k in range(10))
        print('     %-8s $%04X  %s x10 = %r score %s   all ten identical: %s'
              % (CHARNAME[c], b, ' '.join('%02X' % x for x in m[b:b + 6]),
                 nm, sc, allsame))
    print('  -> BIL / BOB / KEV / ARP, 010000 each.  10,000 points is therefore')
    print('     the qualifying score, and it is the same for all four characters.')
    print()
    print('  WHICH TABLE.  $8433 (player 1) / $8453 (player 2) is the character')
    print('  tag, written at COLD BOOT ONLY, by $BE53 LD A,($FFFF) -> $BEE5,')
    print('  which walks $BF19 = 00 8E | 08 D8 | 10 32 | 18 64 counting up to the')
    print('  menu byte.  So ($FFFF) must be 0..3 and the tag is index*8.  The')
    print('  saved states hold ($FFFF) = $2A, the loader stub\'s padding, which')
    print('  walks 42 entries past the end and gives tag $20 -- see "rank".')


# ------------------------------------------------------------------ writers
def watch_run(h, lo, hi, fn):
    h.memobj.watch(lo, hi)
    fn()
    h.memobj.unwatch()
    return list(h.memobj.log)


def summarise(log):
    return collections.Counter('$%04X' % pc for pc, a, v in log)


def cmd_writers():
    print('  WHO EVER WRITES $8826..$891B?  Write-watched on the real Z80.')
    print()
    print('  (1) COLD BOOT: a fresh Harness (entry $8400) run to the attract')
    print('      poll $B47B -- relocation, $B35A, $B470, $869F, $8767.')
    h = Harness()
    log = watch_run(h, TABLE, ENDT, lambda: run_until_pc(h, 0xB47B, limit=40_000_000))
    print('      writes: %s' % (dict(summarise(log)) or 'NONE'))
    print('      -> the defaults are not written by anything.  They are block C\'s')
    print('         own tape bytes, sitting where the loader put them.')
    print()
    print('  (2) LIVE PLAY: 40 main-loop passes from build/state_charsel.pkl.')
    h2 = boot()
    run_until_pc(h2, LOOP_TOP)

    def play():
        for _ in range(40):
            run_until_pc(h2, LOOP_TOP)
    log = watch_run(h2, TABLE, ENDT, play)
    print('      writes: %s' % (dict(summarise(log)) or 'NONE'))
    print()
    print('  (3) THE WHOLE GAME-OVER CYCLE: death -> name entry -> SHIFT ->')
    print('      $B3C6 -> $B35A -> $B470 -> $869F -> $8767 -> the attract poll.')
    h3 = boot()
    die(h3, tag=0x18, score=bcd('034567'))

    def over():
        press(h3, P1['shift']); spin_frames(h3, 30); h3.ports.release_all()
        run_until_pc(h3, 0xB47B, limit=60_000_000)
    log = watch_run(h3, TABLE, ENDT, over)
    print('      writes: %s' % dict(summarise(log)))
    print('      $8764 = the staging key; $874A = the LDDR shift; $8753 = the')
    print('      LDIR that drops the new entry in.  THREE sites, all inside the')
    print('      sort.  Nothing else in the image touches the table.')
    print()
    print('  (4) FOUR MORE attract cycles after that, with $84C6 back to 0:')
    log = watch_run(h3, TABLE, ENDT,
                    lambda: [run_until_pc(h3, DRAW, limit=60_000_000)
                             for _ in range(4)])
    print('      writes: %s' % (dict(summarise(log)) or 'NONE'))
    print('      -> the DRAW is read-only.  $872F zeroes $84C6, so a table is')
    print('         written exactly once per game over, however long the attract')
    print('         screen is left running.')


# ------------------------------------------------------------------ commit
def type_letter(h, m, col, ch):
    """walk the letter at `col` round to `ch` (the alphabet steps on UP)."""
    guard = 0
    while m[NAMEBUF + col] != ord(ch) and guard < 30:
        step_letter(h, 'up'); guard += 1


def cmd_commit():
    print('  FULL COMMITS through the name-entry path.  Each one is: die with a')
    print('  planted score, type a name, SHIFT, and let $B3C6 -> $B35A -> $B470')
    print('  -> $869F run the sort.  The table is diffed across each commit.')
    print()
    h = boot(); m = h.memobj.m
    plan = [('034567', 'AAA'), ('012000', 'BAA'), ('999999', 'CAA'),
            ('010000', 'DAA'), ('005000', 'EAA')]
    prev = bytes(m[TABLE:ENDT])
    for i, (sc, nm) in enumerate(plan):
        if i:
            # back into play: FIRE at the attract screen, then a fresh level
            press(h, P1['fire'])
            run_until_pc(h, LOOP_TOP, limit=80_000_000)
            h.ports.release_all()
        die(h, tag=0x18, score=bcd(sc))
        print('  --- commit %d: elf, score %s, name %s' % (i + 1, sc, nm))
        print('      pending $7F2B: %s   $84C6=%d'
              % (' '.join('%02X' % b for b in m[PEND:PEND + 8]), m[COUNT]))
        for col, ch in enumerate(nm):
            if ch != 'A':
                type_letter(h, m, col, ch)
        press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
        run_until_pc(h, 0xB47B, limit=60_000_000)
        now = bytes(m[TABLE:ENDT])
        ch_ = [TABLE + k for k in range(len(now)) if now[k] != prev[k]]
        print('      %d bytes changed: %s'
              % (len(ch_), ' '.join('$%04X' % a for a in ch_)))
        show(m, 3)
        prev = now
    print()
    print('  Read the last table downward: it is SORTED, high to low, and the')
    print('  005000 commit never appears -- 5,000 does not beat the 10,000 that')
    print('  is still sitting in slot 9.')
    print('  The other three tables are untouched throughout:')
    for c in range(3):
        show(m, c)


# ------------------------------------------------------------------ rank
def call_sort(h, rec, stop=DRAW):
    """poke one 8-byte pending record and run the REAL $869F over it."""
    h.poke(PEND, *rec)
    h.poke(COUNT, 1)
    r = h.sim.registers
    sp = (r[SP] - 2) & 0xFFFF
    h.memobj.m[sp] = 0x00
    h.memobj.m[sp + 1] = 0xFE
    r[SP] = sp
    r[PC] = SORT
    return run_until_pc(h, (stop, 0xFE00), limit=4_000_000)


def rec(score, name='ABC', tag=0x18, millions=0):
    return bytes(bcd(score)) + bytes(ord(c) for c in name) + bytes([tag, millions])


def cmd_rank():
    print('  THE INSERTION RULE, read at $86ED..$8755 and then enumerated by')
    print('  calling the ORIGINAL\'s $869F with a poked pending record.')
    print("""
     $86EB  E = $38                       ; 56 = the byte count for slot 0
     $86ED  loop: HL = $8916 (the new key), IX = the slot, B = 3
     $86F4    A=(HL)&$C0 -> C ; A=(IX)&$C0 ; CP C
              carry (slot < new)  -> $8735 INSERT HERE
              non-zero (slot > new)-> $8716 next slot
              equal                -> next byte, 3 of them
     $8709  then the same over the THREE SCORE BYTES, big-endian
              all six equal        -> falls through to $8716, i.e. NEXT SLOT
     $8716  IX += 6 ; E -= 6 ; no borrow -> loop     (E = 56,50,...,2: TEN slots)
     $8735  LDDR base+53 -> base+59 for E bytes, then LDIR the 6-byte key in

   so the key is (millions, score) BIG-ENDIAN and the LETTERS NEVER RANK.
   A TIE goes AFTER the sitting entry -- the incumbent keeps the higher slot.
   E is 2 more than the number of bytes that actually need to move, so the
   LDDR reads two bytes below the slot and writes two bytes into the slot it
   is about to overwrite; the LDIR then covers them.  Harmless, and it means
   slot 0's shift reads $8824/$8825 -- the last two bytes of the table above.
""")
    h = boot(); m = h.memobj.m
    print('  --- ten distinct scores into the ELF table, in a scrambled order')
    order = ['011000', '099000', '010500', '123456', '010001',
             '020000', '010100', '055555', '010010', '030000']
    for i, sc in enumerate(order):
        call_sort(h, rec(sc, name=chr(0x41 + i) * 3))
    show(m, 3)
    got = [r[2] for r in rows(m, 3)]
    print('      sorted descending: %s' % (got == sorted(got, reverse=True)))
    print('      slot 9 is now %s -- the shipped 010000 has been pushed off'
          % got[9])
    print()
    print('  --- QUALIFICATION: STRICTLY beat slot 9, which ships as 010000')
    for sc in ('000000', '000100', '009999', '010000', '010001', '020000'):
        h2 = boot(); m2 = h2.memobj.m
        before = bytes(m2[TABLE:ENDT])
        call_sort(h2, rec(sc, name='ZZZ'))
        after = bytes(m2[TABLE:ENDT])
        tab = sum(1 for a, b in zip(before[:0xF0], after[:0xF0]) if a != b)
        print('      %s -> %-2d table bytes changed   slot 0 = %s   %s'
              % (sc, tab, fmt_row(rows(m2, 3)[0]),
                 'IN' if tab else 'DISCARDED'))
    print('      (the staging key at $8916 is built either way -- it is the six')
    print('       bytes outside the $F0 counted here.)  10,000 exactly does NOT')
    print('       qualify: the tie rule sends it past all ten slots and off the')
    print('       end.  A dead player with no score is simply not filed.')
    print()
    print('  --- A TIE takes the LOWER slot')
    h3 = boot(); m3 = h3.memobj.m
    call_sort(h3, rec('020000', name='AAA'))
    call_sort(h3, rec('020000', name='BBB'))
    call_sort(h3, rec('020000', name='CCC'))
    show(m3, 3)
    print('      AAA arrived first and holds slot 0; each later 020000 lands')
    print('      BELOW its equals, never above.')
    print()
    print('  --- THE FALL OFF THE END: eleven entries, ten slots')
    h4 = boot(); m4 = h4.memobj.m
    for i in range(11):
        call_sort(h4, rec('%06d' % (900000 - i * 1000), name=chr(0x41 + i) * 3))
    show(m4, 3)
    print('      K (the 11th, 890000) is in; the shipped ARP 010000 is gone.')
    print()
    print('  --- THE CHARACTER TAG picks the table, and $20 is a dead end')
    for tag in (0x00, 0x08, 0x10, 0x18, 0x20):
        h5 = boot(); m5 = h5.memobj.m
        before = bytes(m5[TABLE:ENDT])
        call_sort(h5, rec('222222', name='XYZ', tag=tag))
        after = bytes(m5[TABLE:ENDT])
        hit = [c for c in range(4)
               if before[0x3C * c:0x3C * (c + 1)] != after[0x3C * c:0x3C * (c + 1)]]
        print('      tag $%02X -> base $%04X   tables changed: %s'
              % (tag, 0x8826 + 0x3C * (tag >> 3),
                 [CHARNAME[c] for c in hit] or 'NONE'))
    print('      $20 is what a saved state\'s ($FFFF)=$2A produces.  $8818 then')
    print('      returns $8916, the staging buffer: slot 0 is the key compared')
    print('      with itself (equal, so no insert) and slots 1..9 are the CODE at')
    print('      $891C, whose AND $C0 fields are $C0 -- unbeatable.')
    print()
    print('  --- CORRECTION: "unbeatable" is true of SLOT 1 ONLY, and the guard')
    print('      that actually saves the machine is the MILLIONS field being 0.')
    h7 = boot(); m7 = h7.memobj.m
    print('      the ten 6-byte "slots" $8818 hands the sort for tag $20:')
    for k in range(10):
        a = STAGE + 6 * k
        e = bytes(m7[a:a + 6])
        print('        %d $%04X %s   name fields %s'
              % (k, a, ' '.join('%02X' % b for b in e),
                 ' '.join('%02X' % (b & 0xC0) for b in e[:3])))
    snap = bytes(m7[STAGE:STAGE + 0x80])
    regs0 = list(h7.sim.registers)

    def try20(rc):
        h7.poke(STAGE, *snap)
        h7.sim.registers[:] = regs0
        call_sort(h7, rc)
        return [ENDT + i for i, (a, b) in
                enumerate(zip(snap[6:], bytes(m7[ENDT:STAGE + 0x80]))) if a != b]

    print('      slot 4 ($892E) has name fields 00 00 00, so a key with millions')
    print('      0 ties it there and falls through to the SCORE compare against')
    print('      FD CB 3A.  Sweeping the whole reachable score space at millions 0:')
    bad = [hi for hi in range(0x100)
           if (hi & 15) <= 9 and (hi >> 4) <= 9
           and try20(bytes([hi, 0x99, 0x99]) + b'XYZ' + bytes([0x20, 0]))]
    print('        all 100 BCD values of the top score byte, 99 99 below it:')
    print('        code bytes changed for %s' % (bad or 'NONE of them'))
    print('      and the non-BCD values that WOULD get in:')
    for hi in (0xFD, 0xFE):
        d = try20(bytes([hi, 0x00, 0x00]) + b'XYZ' + bytes([0x20, 0]))
        print('        top score byte $%02X -> %d bytes of LIVE CODE changed'
              % (hi, len(d)))
    print('      now the millions field, 0..7, with the score held at 000000:')
    for mm in range(8):
        d = try20(rec('000000', name='XYZ', tag=0x20, millions=mm))
        print('        millions %d -> %2d code bytes changed%s'
              % (mm, len(d), ('  first $%04X' % d[0]) if d else ''))
    h7.poke(STAGE, *snap)
    print('      -> at millions 0 the score is silently discarded, for EVERY BCD')
    print('         score, and that is the whole of the protection.  At millions')
    print('         1 the key ties slots 3 and 4 on the first two name fields and')
    print('         BEATS slot 4 on the third, so the sort LDIRs six bytes over')
    print('         $892E -- live code inside $8916\'s own routine.  It takes a')
    print('         million points, so no real game reaches it; but "nothing is')
    print('         corrupted" is a statement about millions 0, not about the tag.')
    print()
    print('  --- TWO pending records in one game over ($84C6 can be 2)')
    h6 = boot(); m6 = h6.memobj.m
    h6.poke(PEND, *rec('040000', name='PPP', tag=0x18))
    h6.poke(PEND + 8, *rec('050000', name='QQQ', tag=0x00))
    h6.poke(COUNT, 2)
    r = h6.sim.registers
    sp = (r[SP] - 2) & 0xFFFF
    h6.memobj.m[sp] = 0x00; h6.memobj.m[sp + 1] = 0xFE
    r[SP] = sp; r[PC] = SORT
    run_until_pc(h6, (DRAW, 0xFE00), limit=4_000_000)
    show(h6.memobj.m, 3); show(h6.memobj.m, 0)
    print('      $84C6 is now %d ($872F).  Each record carries its OWN character'
          % m6[COUNT])
    print('      tag, so a two-player game over files the two scores in two')
    print('      different tables.')


# ------------------------------------------------------------------ millions
def cmd_millions():
    print('  THE MILLIONS FIELD.  $B807 adds to the 3-byte packed-BCD score and')
    print('  on overflow calls $B82E, which bumps $84C8 (player 1, IXL&$20) or')
    print('  $84C9 (player 2).  $92B7 patches that byte into $9304 -- the operand')
    print('  of $9303 LD (HL),0 -- so pending record byte +7 is the millions.')
    print('  $86AE then does C = (IX+7) << 2 and $8757 shifts the TOP TWO BITS of')
    print('  C into each name byte, MSB first: SIX bits survive, 0..63.')
    print()
    h = boot(); m = h.memobj.m
    print('  --- packing: drive $869F and read the entry back')
    for mm in (0, 1, 3, 9, 63, 64, 255):
        h2 = boot(); m2 = h2.memobj.m
        call_sort(h2, rec('000001', name='ABC', millions=mm))
        e = bytes(m2[STAGE:STAGE + 6])
        got, nm, sc = unpack(e)
        print('      $84C8=%3d -> key %s  decodes as millions %2d, name %r, score %s'
              % (mm, ' '.join('%02X' % b for b in e), got, nm, sc))
    print('      -> 64 and 255 wrap: the field is 6 bits and the top of the')
    print('         counter is thrown away by the pack, not by the sort.')
    print()
    print('  --- ranking: the millions field is compared BEFORE the score')
    h3 = boot(); m3 = h3.memobj.m
    call_sort(h3, rec('000001', name='BIG', millions=1))
    call_sort(h3, rec('999999', name='SML', millions=0))
    show(m3, 3)
    print('      1,000,001 outranks 999,999, as it must.')
    print()
    print('  --- display: $87DE LD E,0 / LD A,C / CALL $8805 prints ONE NIBBLE')
    print('      of the six-bit field, so the page shows millions mod 16.')
    for mm in (0, 1, 9, 10, 16):
        h4 = boot(); m4 = h4.memobj.m
        call_sort(h4, rec('000001', name='ABC', millions=mm))
        e = bytes(m4[base_of(3):base_of(3) + 6])
        got = unpack(e)[0]
        n = got & 15
        print('      $84C8=%2d -> stored %2d -> nibble %2d -> glyph code $%02X %s'
              % (mm, got, n, 0x30 + n if n else 0x40,
                 'a digit' if n < 10 else ('BLANK ($8805 turns 0 into $10)'
                                           if n == 0 else 'NOT a digit')))
    print('      $8805 adds $30 and calls $B6C0, so 10..15 would print the font\'s')
    print('      codes $3A..$3F -- which are custom fragment glyphs, not digits --')
    print('      and 16 wraps to a blank.  Neither is reachable without ten')
    print('      million points; the field is honest for 0..9.')


# ------------------------------------------------------------------ persist
def cmd_persist():
    print('  DOES THE TABLE SURVIVE A NEW GAME?  $B35A is the whole new-game')
    print('  routine: it zeroes $7F1B..$7F2A and $84C8/$84C9, sets the dungeon to')
    print('  1 and calls $B470.  It does not go near $8826.  Measured:')
    h = boot(); m = h.memobj.m
    die(h, tag=0x18, score=bcd('034567'))
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    run_until_pc(h, 0xB47B, limit=60_000_000)
    after_sort = bytes(m[TABLE:ENDT])
    show(m, 3, 'sorted')
    press(h, P1['fire'])
    run_until_pc(h, LOOP_TOP, limit=80_000_000)
    h.ports.release_all()
    print('     back in play: dungeon %d, score %s, health %02X%02X'
          % (m[0x8403], ' '.join('%02X' % b for b in m[0x8424:0x8427]),
             m[0x8422], m[0x8423]))
    now = bytes(m[TABLE:ENDT])
    print('     table bytes changed by the whole restart: %d'
          % sum(1 for a, b in zip(after_sort, now) if a != b))
    show(m, 3, 'after')
    print()
    print('  So the table persists for as long as the machine is on.  It is')
    print('  block C\'s tape image, written once by the loader; the only way back')
    print('  to BIL/BOB/KEV/ARP is to reload the tape.  There is no reset key and')
    print('  no code path that restores the defaults -- $B3B6 JP $0000 would, but')
    print('  $847D bit 6 is set nowhere in the image, so that arm is dead.')


# ------------------------------------------------------------------ attract
def render_screen(h, path, base=0x4000):
    img = screen.render(h.memobj.m, base, base + 0x1800)
    img = img.resize((img.width * 2, img.height * 2))
    img.save(path)
    return path


def cmd_attract():
    print('  THE ATTRACT DISPLAY.  $B470 is reached from $B374, inside $B35A --')
    print('  which is where a cold boot lands ($BEE2 JP $B358, an LDIR falling')
    print('  into $B35A) AND where every game over lands ($B3C6).  So the ranked')
    print('  table IS the title screen.')
    print("""
     $B470  ($8497) = 0 ; CALL $B5DA ; EI
     $B478  CALL $869F        the sort, then ALWAYS the draw $8767
     $B47B  CALL $9432        both players' join poll / name entry
     $B47E  ($8434 & $8454) bit 7 -> still both dead?  no -> $B48F, leave
     $B487  ($8497) != 0 -> $B47B          spin until the frame counter WRAPS
     $B48D  JR $B478                       -> the next page
     $B48F  BIT 7,($84CC) / RET nz         the REWIND prompt, cold boot only

   $8497 is a byte the ISR bumps once per video frame, so the page period is
   EXACTLY 256 frames (5.11 s at 50.08 Hz).  $8767 bumps $84CB, stores it,
   then RES 2 on the STORED copy: the register keeps 1,2,3,4 and the variable
   holds 1,2,3,0 -- a four-page rotation, WARRIOR, VALKYRIE, WIZARD, ELF.
""")
    h = boot(); m = h.memobj.m
    die(h, tag=0x18, score=bcd('034567'))
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    run_until_pc(h, 0xB35A, limit=60_000_000)
    print('  the page cadence, measured $8767 -> $8767 (T-states / 69888):')
    outs = []
    run_until_pc(h, DRAW, limit=60_000_000)
    last = h.sim.registers[T]
    for i in range(6):
        pc, dtd, n = run_until_pc(h, BLIT, limit=4_000_000)
        page = (m[0x84CB] - 1) & 3
        pc, dtb, n = run_until_pc(h, 0xB47B, limit=4_000_000)
        p = os.path.join(BUILD, 'hs_attract_%d_%s.png'
                         % (i, CHARNAME[page].lower()))
        render_screen(h, p)
        outs.append(p)
        pc, _, _ = run_until_pc(h, DRAW, limit=60_000_000)
        now = h.sim.registers[T]
        print('     page %d  %-8s  $84CB=%d   draw %.2f + blit %.2f frames,'
              '   cycle %7.3f frames'
              % (i, CHARNAME[page], m[0x84CB], dtd / FRAME_T, dtb / FRAME_T,
                 (now - last) / FRAME_T))
        last = now
    print('     -> 256.000, dead on, every time.  The draw and the blit are')
    print('        inside the 256, not on top of it: the loop is not "draw then')
    print('        wait 256", it is "wait for the counter to hit 0".')
    print()
    print('  THE RENDERING.  $87AB draws one row and every glyph goes through')
    print('  $B6C0 -- SUB $20 / ADD A,A / x4 / + $77B0 -- the SAME 8x8 font the')
    print('  HUD uses, glyph = (code-32)*8, eight bytes straight into the shadow')
    print('  screen at $C000 with INC D per pixel row.  One row of the page is')
    print("     $87AD  rank, BCD, two digits, leading zero blanked")
    print("     $87B5  glyph $80 (a full stop) then glyph $40 (blank)")
    print("     $87C2  three letters: (byte & $3F) + $40, so $00 -> '@' = blank")
    print("     $87D9  glyph $40, then ONE nibble of the millions field")
    print("     $87E4  the three score bytes, two BCD digits each")
    print('  $8805 blanks a digit by turning it into $10 -> glyph $40, and sets')
    print('  E once a non-zero digit has appeared, so leading zeros vanish and')
    print('  an all-zero score would print as a single 0.')
    print()
    print('  The character name comes from $B893: tag $00 -> $7E21 "WARRIOR",')
    print('  $08 -> $7E29 (NINE custom glyph codes $81..$89), $10 -> $7E33 (seven')
    print('  codes $29..$2F), $18 -> $7E3B "ELF"; the same call also returns C,')
    print('  the attribute $42/$45/$46/$44 that $877A floods over $D800..$DA7F --')
    print('  the shadow attribute file, 20 rows.  $8A08 centres the name on row 1.')
    print('  $87A8 JP $9CD7 blits the shadow to the real screen.')
    print()
    print('  wrote:')
    for p in outs:
        print('     %s' % p)
    print()
    print('  --- and the same four pages with a PLAYED-IN table, so the layout')
    print('      shows: three-letter names, blanks for SPACE, leading zeros')
    print('      suppressed, and the millions digit in use.')
    played = {
        0: [('IAN', '250000', 0), ('BIL', '210000', 0), ('  A', '198000', 0),
            ('JON', '150000', 0), ('SAM', '099500', 0), ('BIL', '087000', 0),
            ('KAZ', '060000', 0), ('BIL', '033000', 0), ('BIL', '012500', 0),
            ('BIL', '010000', 0)],
        1: [('SUE', '000000', 1), ('BOB', '904000', 0), ('EVE', '450000', 0),
            ('BOB', '300000', 0), ('AMY', '210500', 0), ('BOB', '150000', 0),
            ('BOB', '099000', 0), ('BOB', '045000', 0), ('BOB', '020000', 0),
            ('BOB', '010000', 0)],
        2: [('MEG', '087650', 0), ('KEV', '080000', 0), ('KEV', '070000', 0),
            ('KEV', '060000', 0), ('KEV', '050000', 0), ('KEV', '040000', 0),
            ('KEV', '030000', 0), ('KEV', '020000', 0), ('KEV', '015000', 0),
            ('KEV', '010000', 0)],
        3: [('ZAK', '345000', 0), ('AAA', '034567', 0), ('ARP', '010000', 0),
            ('ARP', '010000', 0), ('ARP', '010000', 0), ('ARP', '010000', 0),
            ('ARP', '010000', 0), ('ARP', '010000', 0), ('ARP', '010000', 0),
            ('ARP', '010000', 0)]}
    for c, ents in played.items():
        for k, (nm, sc, mm) in enumerate(ents):
            h.poke(base_of(c) + 6 * k, *pack(nm, bcd(sc), mm))
    shots = {}
    for i in range(4):
        run_until_pc(h, DRAW, limit=60_000_000)
        page = (m[0x84CB] - 1) & 3
        run_until_pc(h, 0xB47B, limit=4_000_000)
        p = os.path.join(BUILD, 'hs_page_%s.png' % CHARNAME[page].lower())
        render_screen(h, p)
        shots[page] = screen.render(m, 0x4000, 0x5800)
        print('     %s' % p)
    try:
        from PIL import Image
        sheet = Image.new('RGB', (256 * 2 + 12, 192 * 2 + 12), (24, 24, 24))
        for c in range(4):
            if c in shots:
                sheet.paste(shots[c], (4 + (c % 2) * 260, 4 + (c // 2) * 196))
        sheet = sheet.resize((sheet.width * 2, sheet.height * 2))
        out = os.path.join(BUILD, 'hs_table_pages.png')
        sheet.save(out)
        print('     %s   <- the four pages of the rotation, one sheet' % out)
    except Exception as e:                                    # noqa: BLE001
        print('     (no sheet: %s)' % e)


# ------------------------------------------------------------------ display
def cmd_display():
    print('  THE DRAW, $8767..$8817, is READ-ONLY and it DOES NOT SORT.  The')
    print('  table is kept in order by the insert; the page just walks ten slots')
    print('  and prints what it finds.  Poke slot 0 low and slot 1 high and the')
    print('  page prints them in that order:')
    h = boot(); m = h.memobj.m
    die(h, tag=0x18, score=bcd('034567'))
    press(h, P1['shift']); spin_frames(h, 30); h.ports.release_all()
    run_until_pc(h, 0xB47B, limit=60_000_000)     # past the sort: $84C6 is 0
    h.poke(base_of(3), *pack('LOW', bcd('000100')))
    h.poke(base_of(3) + 6, *pack('HGH', bcd('987600')))
    before = bytes(m[TABLE:ENDT])
    for _ in range(2):
        run_until_pc(h, DRAW, limit=60_000_000)
        run_until_pc(h, 0xB47B, limit=4_000_000)
        run_until_pc(h, DRAW, limit=60_000_000)
        run_until_pc(h, 0xB47B, limit=4_000_000)
    print('     after two full rotations the table is byte-identical: %s'
          % (bytes(m[TABLE:ENDT]) == before))
    show(m, 3)
    p = os.path.join(BUILD, 'hs_unsorted.png')
    for _ in range(4):
        run_until_pc(h, DRAW, limit=60_000_000)
        run_until_pc(h, 0xB47B, limit=4_000_000)
        if ((m[0x84CB] - 1) & 3) == 3:            # $84CB is set by the draw
            break
    render_screen(h, p)
    print('     %s   <- and it shows on screen in slot order' % p)
    print()
    print('  THE GEOMETRY, read off $879A LD DE,$C087 and $87F2\'s row step:')
    print('     the shadow screen is $C000 and its attributes $D800, laid out')
    print('     exactly like $4000/$5800.  $C087 is character row 4, column 7.')
    print('     $87F2 does E += $20 and, on the wrap, D += 8 -- the standard')
    print('     third-crossing step -- so the ten entries occupy character rows')
    print('     4..13.  Each row is FIFTEEN glyphs, columns 7..21:')
    print('       col  7  8   rank, two BCD digits, leading zero blanked')
    print('       col  9      glyph $80, a full stop')
    print('       col 10      glyph $40, blank')
    print('       col 11..13  the three letters')
    print('       col 14      glyph $40, blank')
    print('       col 15      the millions nibble (blank when 0)')
    print('       col 16..21  the six score digits, leading zeros blanked')
    print('     $877A floods $D800..$DA7F -- 640 bytes = attribute rows 0..19 --')
    print('     with $B893\'s colour, so the whole playfield takes one colour and')
    print('     the four HUD rows 20..23 are left to $9432/$92A6 ("PRESS FIRE").')
    for c, at in ((0, 0x42), (1, 0x45), (2, 0x46), (3, 0x44)):
        print('       %-8s attribute $%02X = BRIGHT %s on black'
              % (CHARNAME[c], at,
                 ['black', 'blue', 'red', 'magenta', 'green', 'cyan',
                  'yellow', 'white'][at & 7]))
    print()
    print('  THE FONT IS THE HUD\'S.  $B6C0 is the ONLY glyph printer in the')
    print('  image and $77B0 is loaded at exactly one instruction ($B6C9):')
    print('     glyph = $77B0 + (code - $20)*8, eight bytes, INC D per pixel row.')
    print('     15 call sites -- $87B7/$87BC/$87D4/$87DB (this page), $8815')
    print('     (digits), $8A08 (centred strings), $8B10/$8B18, $92D2/$939C (the')
    print('     name entry), $B617, $B699, $B86D/$B883 (the HUD score and health)')
    print('     and $B785.  So the ranked table, the name entry and the in-game')
    print('     panel are one font and one printer, not three.')
    print('     Note the game\'s BLANK is code $40, not $20: font index 32 is an')
    print('     empty cell, which is why $87C2\'s "letter + $40" turns a SPACE')
    print('     (letter code 0) into a blank, and why $8805 blanks a digit by')
    print('     making it $10 before the ADD A,$30.')


CMDS = {'layout': cmd_layout, 'writers': cmd_writers, 'commit': cmd_commit,
        'rank': cmd_rank, 'millions': cmd_millions, 'persist': cmd_persist,
        'display': cmd_display, 'attract': cmd_attract}


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(CMDS) if what == 'all' else [what]
    for nm in names:
        print('=== %s ===' % nm)
        CMDS[nm]()
        print()


if __name__ == '__main__':
    main()
