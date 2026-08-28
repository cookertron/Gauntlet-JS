#!/usr/bin/env python3
"""
mkimage.py -- build the post-loader 64K memory image directly (manual 6.1).

We ASSERT the loader's postcondition rather than running the loader.  Every
line below cites the instruction in the relocated stub at $FF00 that it comes
from; the stub was recovered in phase 2 (see NOTES-loader.md).

    $FF00  LD SP,$5C00
    $FF03  LD IX,$8600 / LD DE,$5210 / LD A,$80 / CALL $FF3B   block A -> $8600
    $FF0F  CALL $C1F2                                          (inside block A)
    $FF12  65536-iteration delay, border 0
    $FF20  LD A,$81 / LD IX,$73DA / LD DE,$0B41 / CALL $FF3B   block B -> $73DA
    $FF2C  LD A,$82 / LD IX,$8400 / LD DE,$7777 / CALL $FF3B   block C -> $8400
    $FF38  JP $8400                                            entry point

Block C overlaps and overwrites block A entirely ($8400..$FB76 covers
$8600..$D80F), so block A is a transient stage: it is loaded, $C1F2 is called,
and then it is gone.  Anything block A leaves behind must be OUTSIDE
$8400..$FB76, and that is what the --stage a image is for.

Usage:
    python tools/mkimage.py            build/image.bin        (post-loader, at JP $8400)
    python tools/mkimage.py --stage a  build/image_a.bin      (block A only, at CALL $C1F2)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
B1 = os.path.join(ROOT, 'build', 'blocks1')


def body(n):
    return open(os.path.join(B1, f'Gauntlet_-_Side_1.b{n:02d}.body.bin'), 'rb').read()


BLOCK_A = (0x8600, 0x5210, 3)      # dest, len, tzx block index
BLOCK_B = (0x73DA, 0x0B41, 5)
BLOCK_C = (0x8400, 0x7777, 6)
ENTRY = 0x8400


def place(mem, spec):
    dest, ln, idx = spec
    data = body(idx)
    assert len(data) == ln, f'block {idx}: payload {len(data)} != loader DE ${ln:04X}'
    mem[dest:dest + ln] = data
    return f'block #{idx}: ${dest:04X}..${dest+ln-1:04X}  ({ln} bytes)'


def build(stage):
    mem = bytearray(0x10000)
    lines = []
    if stage == 'a':
        lines.append(place(mem, BLOCK_A))
    else:
        lines.append(place(mem, BLOCK_A))
        lines.append(place(mem, BLOCK_B))
        lines.append(place(mem, BLOCK_C))
    # the loader stub itself survives at $FF00 (block C ends at $FB76)
    basic = open(os.path.join(B1, 'Gauntlet_-_Side_1.b02.body.bin'), 'rb').read()
    stub = basic[0x5CF1 - 0x5CCB:0x5CF1 - 0x5CCB + 0x100]
    mem[0xFF00:0x10000] = stub
    lines.append('loader stub: $FF00..$FFFF (survives; block C ends at $FB76)')
    return mem, lines


if __name__ == '__main__':
    stage = 'full'
    args = sys.argv[1:]
    if '--stage' in args:
        i = args.index('--stage'); stage = args[i + 1]; del args[i:i + 2]
    mem, lines = build(stage)
    out = os.path.join(ROOT, 'build', 'image.bin' if stage == 'full' else f'image_{stage}.bin')
    open(out, 'wb').write(mem)
    for l in lines:
        print(l)
    print(f'entry ${ENTRY:04X}' if stage != 'a' else 'stage A: entry is CALL $C1F2')
    print(f'wrote {out}')
