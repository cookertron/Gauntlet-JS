#!/usr/bin/env python3
"""packdump.py -- load a side-2 pack into $C000 exactly as the game does and
dump / disassemble its leading stub."""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness   # noqa: E402
import adis                   # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def main():
    n = int(sys.argv[1], 0) if len(sys.argv) > 1 else 1
    count = int(sys.argv[2], 0) if len(sys.argv) > 2 else 90
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    m[0xC000:0xD010] = bytes(0x1010)
    path = os.path.join(ROOT, 'build', 'blocks2',
                        'Gauntlet_-_Side_2.b%02d.body.bin' % n)
    pack = open(path, 'rb').read()
    m[0xC000:0xC000 + len(pack)] = pack
    print('pack %d, %d bytes, last byte $%02X' % (n, len(pack), pack[-1]))
    for i in range(6):
        m[0xC000 + i] = 0
    adis.disasm(m, 0xC000, count, show_data=False)


if __name__ == '__main__':
    main()
