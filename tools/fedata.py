#!/usr/bin/env python3
"""
fedata.py -- extract THE FRONT END's assets and tables into build/fe_data.json.

WHAT THE FRONT END IS.  The loader's `$FF0F CALL $C1F2` runs block A, the
transient stage that puts up the loading screen, plays the title tune, shows
the credits and keys pages, and ASKS THE FOUR QUESTIONS whose answers are the
only thing the game ever learns from it:

    $FFFF  player 1's character   0..3    written $C42C from ($C7FD)
    $FFFE  player 2's character   0..3    written $C449 (1P: LD A,R) / $C4E3
    $FFFC  player 1's control     0..3    written $C508 from ($C808)
    $FFFB  player 2's control     0..3    written $C51D from ($C808)

plus $FFFD, the sound branch, which is not a question but the $7FFD paging
probe's answer ($C242).  Block C then loads on top of block A and the whole
front end ceases to exist, so everything here has to come out of the block-A
image (build/image_a.bin, base $8600) rather than out of a live game.

Everything below is READ FROM THE TAPE IMAGE at an address recovered from a
disassembly taken from a known instruction boundary.  Nothing is eyeballed off
a screenshot.

    python tools/fedata.py            write build/fe_data.json
    python tools/fedata.py --check    print what it found and check it
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

IMAGE_A = os.path.join(ROOT, 'build', 'image_a.bin')
IMAGE_C = os.path.join(ROOT, 'build', 'image.bin')
OUT = os.path.join(ROOT, 'build', 'fe_data.json')

SCREEN = 0x1B00                  # 6,912 = 6,144 bitmap + 768 attributes

# --- addresses, every one of them from the disassembly ----------------------
LOADING_SCREEN = 0x8600          # $C254 LD HL,$8600 / LD BC,$1B00 / LD DE,$4000
TITLE_SCREEN = 0xA500            # $C2EE LD HL,$A500 / LD DE,$4000 / LD BC,$1B00
FE_FONT = 0xA100                 # $C8AD LD HL,$A100, indexed by (code - $20)*8
PAGE_CREDITS = 0xCE71            # $C2CC LD IX,$CE71 / CALL $C83C
PAGE_KEYS = 0xD189               # $C2D6 LD IX,$D189
PAGE_CONTROLS = 0xD4C2           # $C4EB LD IX,$D4C2
PAGE_BYTES = 24 * 33             # $C83C: 24 rows of (32 chars + 1 row attribute)

PICKER_CORNERS = 0xC800          # 4 x 2, the 2x2 highlight's attribute address
PICKER_RAMP = 0xC80A             # 14 bytes, the ink ramp, one value per frame
PICKER_PAPERS = 0xC818           # 4 bytes, the quadrant paper
CTRL_ROWS = 0xC81C               # 4 x 2, the control marker's attribute address

TUNE_PITCH = 0xC0D4              # $C037 LD HL,$C0D4 / ADD HL,DE, index note+12
TUNE_CH1 = 0xC10A                # $C000 LD HL,$C109 -> ($C01B); first read +1
TUNE_CH2 = 0xC173                # $C006 LD HL,$C172 -> ($C01F)
TUNE_END = 0x40                  # $C029 CP $40 / JR z,$C03F

# block C's four ranked tables.  Block C loads at $8400, so $8826 is offset
# $0426 into the block.  $8818 reaches them as $87EA + $3C*(tag/8 + 1).
HS_BASE = 0x8826
HS_STRIDE = 0x3C                 # 60 = 10 entries x 6 bytes
HS_TABLES = 4

# $B893's fall-through chain: tag -> (length-prefixed name string, attribute)
CLASS_NAMES = [(0x00, 0x7E21, 0x42), (0x08, 0x7E29, 0x45),
               (0x10, 0x7E33, 0x46), (0x18, 0x7E3B, 0x44)]

# $B494's three length-prefixed lines, centred by $8A08 at shadow offsets
# $00C0, $0800 and $0840 -- char rows 6, 8 and 10.
REWIND = (0xB4B7, (0x00C0, 0x0800, 0x0840))


def b64(bs):
    return base64.b64encode(bytes(bs)).decode('ascii')


def inline(img, call_addr):
    """The text of the `CALL $C895` at `call_addr`.

    $C895  POP IX / <plot until NUL> / INC IX / JP (IX)
    so the string lives IN the instruction stream, immediately after the CALL,
    and is NUL terminated.  Asserting the opcode is what stops this reading a
    random run of bytes as text."""
    assert img[call_addr] == 0xCD and img[call_addr+1] == 0x95 \
        and img[call_addr+2] == 0xC8, \
        'no CALL $C895 at $%04X' % call_addr
    a = call_addr + 3
    s = a
    while img[a] != 0x00:
        a += 1
    return img[s:a].decode('latin1')


def hl_of(img, ld_addr):
    """The operand of an `LD HL,nn` (opcode $21) -- the screen address the
    following CALL $C895 prints at."""
    assert img[ld_addr] == 0x21, 'no LD HL,nn at $%04X' % ld_addr
    return img[ld_addr+1] | (img[ld_addr+2] << 8)


def box(img, pairs):
    """One 12x6 menu box: six (LD HL,nn ; CALL $C895) pairs."""
    return [[hl_of(img, ld), inline(img, ld + 3)] for ld in pairs]


def main():
    a = open(IMAGE_A, 'rb').read()
    c = open(IMAGE_C, 'rb').read()
    assert len(a) == 0x10000 and len(c) == 0x10000

    d = {}

    # ---- the two full-screen pictures --------------------------------------
    d['loading_screen'] = b64(a[LOADING_SCREEN:LOADING_SCREEN + SCREEN])
    d['title_screen'] = b64(a[TITLE_SCREEN:TITLE_SCREEN + SCREEN])
    # the front end's OWN 8x8 font, $20..$9F -- 128 glyphs, and it ends exactly
    # where the title screen begins ($A100 + $400 = $A500)
    assert FE_FONT + 0x400 == TITLE_SCREEN
    d['font'] = b64(a[FE_FONT:FE_FONT + 0x400])
    d['font_first'] = 0x20

    # ---- the three text pages ----------------------------------------------
    d['pages'] = {
        'credits': b64(a[PAGE_CREDITS:PAGE_CREDITS + PAGE_BYTES]),
        'keys': b64(a[PAGE_KEYS:PAGE_KEYS + PAGE_BYTES]),
        'controls': b64(a[PAGE_CONTROLS:PAGE_CONTROLS + PAGE_BYTES]),
    }
    # $CE71 + $318 == $D189 exactly, which is the arithmetic check that 33
    # bytes a row is right and 64 (32 chars + 32 attributes) is not.
    assert PAGE_CREDITS + PAGE_BYTES == PAGE_KEYS, 'the page stride moved'

    # ---- the character picker's own tables ---------------------------------
    d['picker'] = {
        'corners': [a[PICKER_CORNERS + 2*i] | (a[PICKER_CORNERS + 2*i + 1] << 8)
                    for i in range(4)],
        'papers': list(a[PICKER_PAPERS:PICKER_PAPERS + 4]),
        'ramp': list(a[PICKER_RAMP:PICKER_RAMP + 14]),
        'ctrl_rows': [a[CTRL_ROWS + 2*i] | (a[CTRL_ROWS + 2*i + 1] << 8)
                      for i in range(4)],
        # $C7FD's TAPE VALUE.  Nothing writes it between $C1F2 and $C426 (write
        # -watched over a complete blind run), so this is the menu's default.
        'cursor_default': a[0xC7FD],
        'forbidden_default': a[0xC7FE],
        'players_default': a[0xC7FF],
        'ctrl_default': a[0xC808],
    }
    assert d['picker']['cursor_default'] == 0, 'the picker default moved'

    # ---- the menu boxes, read out of the instruction stream ----------------
    d['boxes'] = {
        # $C307..$C378  "ONE OR TWO / PLAYERS"
        'oneortwo': box(a, [0xC307, 0xC31A, 0xC32D, 0xC340, 0xC353, 0xC366]),
        # $C396..$C407  "PLAYER ONE / CHOOSE"
        'p1choose': box(a, [0xC396, 0xC3A9, 0xC3BC, 0xC3CF, 0xC3E2, 0xC3F5]),
        # $C452..$C4C3  "PLAYER TWO / CHOOSE"
        'p2choose': box(a, [0xC452, 0xC465, 0xC478, 0xC48B, 0xC49E, 0xC4B1]),
    }
    d['stoptape'] = [hl_of(a, 0xC1FD), inline(a, 0xC200)]
    d['pressplay'] = [hl_of(a, 0xC520), inline(a, 0xC523)]

    # ---- the title tune ($C000), 48K arm -----------------------------------
    # $C26D..$C279 patch three constants for the 48K:
    #   ($C01A) := 0     the OUT ($FE) base -- unpatched $03 gives a MAGENTA border
    #   ($C023) := $E6   the tempo/length constant, 238 -> 230
    #   ($C011) := $C5B9 the abort test, ROM KEY-SCAN $028E -> "SPACE only"
    assert a[0xC26E] == 0x32 and (a[0xC26F] | a[0xC270] << 8) == 0xC01A
    assert a[0xC271] == 0x3E and a[0xC272] == 0xE6
    ch1 = []
    p = TUNE_CH1
    while a[p] != TUNE_END:
        ch1.append(a[p]); p += 1
    ch1_end = p
    ch2 = []
    p = TUNE_CH2
    while a[p] != TUNE_END:
        ch2.append(a[p]); p += 1
    ch2_end = p
    # $C031 is  LD A,(HL) / ADD A,12 / LD E,A / LD D,0 / LD HL,$C0D4 /
    # ADD HL,DE / LD H,(HL) / LD L,1, and A IS EIGHT BITS: the index is
    # (note + 12) & $FF, so a note byte of $FE indexes 10, not 266.
    idx = sorted({(n + 12) & 0xFF for n in ch1 + ch2})
    lo, hi = idx[0], idx[-1]
    # $C05F RL E / JP c,$C10A -- an escape taken when the index has bit 7 set.
    # $C10A is also channel 1's first note, so taking it would execute the
    # note stream as code.  It never fires with this data; assert that.
    assert hi < 0x80, 'a note indexes >= $80 and $C05F would escape to $C10A'
    d['tune'] = {
        'ch1': ch1, 'ch2': ch2,
        'pitch': list(a[TUNE_PITCH:TUNE_PITCH + hi + 1]),
        'pitch_bias': 12,                       # $C032 ADD A,12
        'tempo': a[0xC272],                     # the byte $C271 LD A,$E6 stores
        'out_base': 0,                          # $C26D SUB A / LD ($C01A),A
        'toggle': 0x10,                         # $C083 LD D,$10
        'ch1_end': ch1_end, 'ch2_end': ch2_end,
        'note_lo': lo, 'note_hi': hi,
    }

    # ---- block C: the four ranked tables and the class names ---------------
    d['hiscore'] = {
        'base': HS_BASE, 'stride': HS_STRIDE,
        'tables': [list(c[HS_BASE + HS_STRIDE*i: HS_BASE + HS_STRIDE*(i+1)])
                   for i in range(HS_TABLES)],
    }
    for t in d['hiscore']['tables']:
        assert len(t) == 60
    d['class_names'] = [
        {'tag': tag, 'ink': ink, 'codes': list(c[addr+1: addr+1+c[addr]])}
        for tag, addr, ink in CLASS_NAMES]

    # $B494 -- the cold-boot-only REWIND prompt, three length-prefixed lines
    p, lines = REWIND[0], []
    for off in REWIND[1]:
        n = c[p]
        lines.append([off, list(c[p+1:p+1+n])])
        p += 1 + n
    d['rewind'] = {'lines': lines, 'attr': 0x47}   # $8B8E floods $D800 with $47

    json.dump(d, open(OUT, 'w'), separators=(',', ':'))
    print('wrote %s (%d bytes)' % (OUT, os.path.getsize(OUT)))
    check(d)


def check(d):
    print('  loading screen  6,912 bytes at $%04X' % LOADING_SCREEN)
    print('  title  screen   6,912 bytes at $%04X' % TITLE_SCREEN)
    print('  front-end font  1,024 bytes at $%04X, 128 glyphs $20..$9F'
          % FE_FONT)
    print('  text pages      3 x %d bytes (24 rows of 32 chars + 1 attribute)'
          % PAGE_BYTES)
    p = d['picker']
    print('  picker corners  %s' % ' '.join('$%04X' % v for v in p['corners']))
    print('    as (row,col)  %s'
          % ' '.join('(%d,%d)' % ((v - 0x5800) // 32, (v - 0x5800) % 32)
                     for v in p['corners']))
    print('  picker papers   %s' % ' '.join('$%02X' % v for v in p['papers']))
    print('  picker ramp     %s' % ' '.join('%02X' % v for v in p['ramp']))
    print('  control rows    %s' % ' '.join('$%04X' % v for v in p['ctrl_rows']))
    print('  DEFAULTS from the tape image: cursor=%d players=%d control=%d'
          % (p['cursor_default'], p['players_default'], p['ctrl_default']))
    for k, v in d['boxes'].items():
        print('  box %-9s %s' % (k, ' | '.join(t.strip() for _, t in v if t.strip())))
    print('  stoptape  $%04X %r' % (d['stoptape'][0], d['stoptape'][1]))
    print('  pressplay $%04X %r' % (d['pressplay'][0], d['pressplay'][1]))
    t = d['tune']
    print('  tune: channel 1 %d notes, channel 2 %d notes, pitch table %d bytes,'
          ' tempo $%02X' % (len(t['ch1']), len(t['ch2']), len(t['pitch']),
                            t['tempo']))
    print('        note byte range %d..%d -> pitch index %d..%d'
          % (min(t['ch1'] + t['ch2']), max(t['ch1'] + t['ch2']),
             t['note_lo'], t['note_hi']))
    for i, tb in enumerate(d['hiscore']['tables']):
        row = tb[0:6]
        name = ''.join(chr(0x40 + (b & 0x3F)) if (b & 0x3F) else ' '
                       for b in row[0:3])
        print('  ranked table %d at $%04X: %r %02X%02X%02X x%d'
              % (i, HS_BASE + 60*i, name, row[3], row[4], row[5],
                 sum(1 for j in range(10) if tb[6*j:6*j+6] == row)))
    for cn in d['class_names']:
        print('  class tag $%02X ink $%02X codes %s'
              % (cn['tag'], cn['ink'], ' '.join('%02X' % x for x in cn['codes'])))


if __name__ == '__main__':
    if '--check' in sys.argv:
        check(json.load(open(OUT)))
    else:
        main()
