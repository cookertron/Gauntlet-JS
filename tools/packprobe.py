#!/usr/bin/env python3
"""packprobe.py -- inspect the live state around the dungeon-pack load."""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness   # noqa: E402


def hexd(m, a, n, per=32):
    for i in range(0, n, per):
        print('%04X: %s' % (a + i, ' '.join('%02X' % b for b in m[a + i:a + i + per])))


def main():
    h = Harness()
    st = pickle.load(open(os.path.join(ROOT, 'build', 'state_charsel.pkl'), 'rb'))
    h.load_state(st)
    m = h.memobj.m
    print('deck pos', h.deck.pos, 'PC %04X' % h.pc())
    print('level ($8403) =', m[0x8403])
    print('$84CC =', hex(m[0x84CC]), ' $84CD(IY+4E) =', hex(m[0x84CD]))
    pack = open(os.path.join(ROOT, 'build', 'blocks2',
                             'Gauntlet_-_Side_2.b01.body.bin'), 'rb').read()
    print('pack len', len(pack))
    # where does the pack live in RAM now?
    mm = bytes(m)
    for probe_len in (32, 16, 8):
        idx = []
        p = 0
        while True:
            p = mm.find(pack[:probe_len], p)
            if p < 0:
                break
            idx.append(p)
            p += 1
        print('first %d bytes of pack found at' % probe_len,
              ['%04X' % i for i in idx])
        if idx:
            break
    print()
    print('--- $6F80 (source of the $91E1 LDIR), 96 bytes')
    hexd(m, 0x6F80, 96)
    print('--- $DDD8, 64 bytes')
    hexd(m, 0xDDD8, 64)


if __name__ == '__main__':
    main()
