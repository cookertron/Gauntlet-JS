#!/usr/bin/env python3
"""
sfxdata.py -- extract the 18 AY sound-effect streams into build/sfx_data.json.

    python tools/sfxdata.py            write build/sfx_data.json
    python tools/sfxdata.py --print    print the table and the row counts

WHAT IS BEING EXTRACTED, and how it was established (all first hand, on the
real Z80 through tools/harness.py; the reproduction is tools/soundgate.py):

  $73DA is a table of EIGHTEEN 16-BIT OFFSETS -- not addresses.  $BABE loads
  BC = $73DA and does ADD HL,BC twice, so entry i is at $73DA + 2*i and the
  word there is added back to $73DA to give the stream:

      $BAB7  LD (IX+3),A / ADD A,A / LD L,A / LD H,0
      $BABE  LD BC,$73DA / ADD HL,BC        ; HL = $73DA + 2*id
      $BAC2  LD A,(HL) / INC HL / LD H,(HL) / LD L,A
      $BAC6  ADD HL,BC                      ; HL = $73DA + offset

  A stream is a run of TWO-BYTE ROWS terminated by a $00 byte, one row per
  50 Hz frame ($BB35 consumes exactly one row per call and $BADB calls it
  once per video frame from the ISR at $A2A5).  This file ships the ROW
  BYTES, not a decode: the arithmetic that turns them into register values
  lives in the engine beside the address it came from ($BB35), where it can
  be read against the disassembly.  For the record it is

      byte 0 == 0            END: write volume 0 to R(8+ch) and release
      byte 0 bits 7..4       volume        -> R(8+ch)
      byte 0 bits 3..0       n, R6 = 2*n   ($BB62 ADD A,A), noise ENABLED
                             for this channel iff n != 0 ($BB64 JR z)
      byte 1                 p, tone period = ((p+1) & $FF) * 4
                             ($BB71 INC A is EIGHT BIT, so p = $FF gives
                              period 0, not 1024 -- ids $0F and $11 use it)

THE THREE INVARIANTS ASSERTED HERE, none of which comes from the engine:

  1. the 18 words occupy $73DA..$73FD and the lowest stream starts at $73FE,
     immediately after the table;
  2. the 18 streams TILE $73FE..$77AF with zero gaps and zero overlaps -- a
     wrong row size could not do that, and it is what fixes the id space at
     0..$11 by construction rather than by argument;
  3. the row counts are 8,8,14,20,5,25,54,28,7,30,8,51,25,15,38,85,35,8 = 464,
     which is the number of frames tools/soundgate.py's register differential
     compares against the running original.

The source image is build/live_cs.bin, the post-relocation dump, because the
sound data is inside the 2,881-byte tape block B that is LDIR'd about at boot
($73DA is where it ends up and where $BABE reads it).  Cross-checked against
build/blkB.bin, the tape bytes, in --print mode.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, 'build', 'live_cs.bin')
OUT = os.path.join(ROOT, 'build', 'sfx_data.json')

TABLE = 0x73DA
COUNT = 18
# measured with tools/soundgate.py rows (and independently by walking the
# streams here); quoted so that a silently-changed image fails the build
ROWS_EXPECTED = [8, 8, 14, 20, 5, 25, 54, 28, 7, 30, 8, 51, 25, 15, 38, 85,
                 35, 8]


def extract(img):
    effects = []
    for i in range(COUNT):
        p = TABLE + 2 * i
        off = img[p] | (img[p + 1] << 8)
        addr = (TABLE + off) & 0xFFFF
        rows, q = [], addr
        while img[q] != 0:
            rows.append([img[q], img[q + 1]])
            q += 2
        effects.append({'id': i, 'addr': addr, 'end': q + 1, 'rows': rows})
    return effects


def check(effects):
    # 1 -- the table is immediately followed by the lowest stream
    lo = min(e['addr'] for e in effects)
    assert lo == TABLE + 2 * COUNT, (
        'the streams do not start at $%04X' % (TABLE + 2 * COUNT))
    # 2 -- the streams tile their range with no gap and no overlap
    spans = sorted((e['addr'], e['end']) for e in effects)
    for a, b in zip(spans, spans[1:]):
        assert a[1] == b[0], 'gap/overlap at $%04X..$%04X vs $%04X' % (
            a[0], a[1], b[0])
    # 3 -- the row counts
    got = [len(e['rows']) for e in effects]
    assert got == ROWS_EXPECTED, 'row counts moved: %s' % got
    return lo, spans[-1][1]


def main():
    img = open(LIVE, 'rb').read()
    effects = extract(img)
    lo, hi = check(effects)
    total = sum(len(e['rows']) for e in effects)

    if '--print' in sys.argv:
        blkb = os.path.join(ROOT, 'build', 'blkB.bin')
        note = ''
        if os.path.exists(blkb):
            tape = open(blkb, 'rb').read()
            # block B loads at $73DA, so the tape byte for address a is
            # tape[a - $73DA]
            same = all(tape[e['addr'] - TABLE + k] == b
                       for e in effects
                       for k, b in enumerate(sum(e['rows'], [])))
            note = '   (tape block B agrees: %s)' % same
        print('table $%04X..$%04X, streams $%04X..$%04X, %d rows%s'
              % (TABLE, TABLE + 2 * COUNT - 1, lo, hi - 1, total, note))
        for e in effects:
            print('  id $%02X  $%04X  %3d rows  %5.0f ms'
                  % (e['id'], e['addr'], len(e['rows']),
                     1000 * len(e['rows']) / 50.08))
        return

    data = {
        '_source': 'build/live_cs.bin, table $73DA (18 relative words)',
        '_format': ('rows are the game\'s own two bytes: '
                    'b0 = volume<<4 | noiseperiod/2, b1 = tone byte; '
                    'the decode lives in the engine at $BB35'),
        'table': TABLE,
        'span': [lo, hi],
        'effects': [{'id': e['id'], 'addr': e['addr'], 'rows': e['rows']}
                    for e in effects],
    }
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    print('wrote %s: %d effects, %d rows, streams $%04X..$%04X, 0 gaps'
          % (OUT, COUNT, total, lo, hi - 1))


if __name__ == '__main__':
    main()
