#!/usr/bin/env python3
"""
packexport.py -- ship the DUNGEONS.

Writes build/packdata.json: the 307 dungeon records that live on side 2 of the
tape, as their own bytes, plus the pack/sub-block index the game's selector
($9175) walks.  The engine decodes them with its own port of the original's
expander $97CB -- so what ships is the ORIGINAL'S DATA and the ORIGINAL'S
ALGORITHM, not a captured picture of one map.

WHY THE RECORD BYTES AND NOT THE DECODED GRIDS
    * a grid is 1024 bytes; the record it came from averages 289.  All 307
      grids would be 314 KB, the 125 distinct records are 35,697 bytes.
    * $9B5F (which $36 survives), $9BB7 (the level>=8 extra objects) and the
      two mirrors $9C06/$9C69 all run AFTER the expander and are RNG-driven,
      so a shipped grid would have to be one particular roll of them.  The
      engine runs those passes itself, exactly where the original runs them.
    * it makes the LEVEL CLOSURE TEST a test of the decoder, not of a copy.

WHAT A "RECORD" IS  (the pack-format angle closed this; re-checked here)
    A side-2 block is a PACK.  +$0B9 holds ten 16-bit sub-block lengths and
    +$0D0 the sub-blocks themselves, back to back; the last byte of the block
    is $FF.  A sub-block IS one record, self-delimiting:
        +0  length low byte      (9th bit = bit 7 of +2)
        +1  flags
        +2  b7 length high bit, b3-5 colour scheme
        +3  vector-table length in bytes
        +4.. the vector table, then the RLE cell stream
    ASSERTED HERE, for all 31 packs: $0D0 + sum(lengths) == len(pack) - 1, the
    last byte is $FF, and each record's own +0/+2 length equals the pack's
    table entry.  ASSERTED for all 307 records: 4 + hdrlen + rle == length
    exactly, and the RLE body is long enough that the vector walker's 3-byte
    lookahead can never leave the record.

    Pack 1 (tape flag $80) holds 7 records and is dungeons 1-7 verbatim.
    Packs 2-31 (flag $C0) hold 10 each: 0-7 ordinary, 8 and 9 the treasure
    rooms ($C09F: AND 1 / ADD A,8).

Usage:  python tools/packexport.py
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import packdecode as PD                                   # noqa: E402

OUT = os.path.join(ROOT, 'build', 'packdata.json')


def slices(packn):
    """(offset, length) of every sub-block of a pack, from its own table."""
    pack = PD.load_pack(packn)
    lens = PD.sub_lengths(pack)
    off = PD.HDR
    out = []
    for n in lens:
        if n:
            out.append((off, n))
        off += n
    # the container cross-check, done here rather than taken on trust
    assert off == len(pack) - 1, (packn, off, len(pack))
    assert pack[-1] == 0xFF, (packn, pack[-1])
    return pack, out


def main():
    records = []                 # distinct record byte-strings
    index = {}
    packs = []
    nslots = 0
    for n in range(1, 32):
        pack, sl = slices(n)
        ids = []
        for off, ln in sl:
            body = bytes(pack[off:off + ln])
            # the record's own length byte must agree with the pack's table
            assert body[0] + (0x100 if body[2] & 0x80 else 0) == ln, (n, off)
            hdrlen = body[3]
            rle = ln - 4 - hdrlen
            assert rle > 3, (n, off, hdrlen, ln)   # the 3-byte lookahead is safe
            key = body
            if key not in index:
                index[key] = len(records)
                records.append(key)
            ids.append(index[key])
            nslots += 1
        packs.append(ids)
    assert nslots == 307, nslots
    assert len(packs[0]) == 7 and all(len(p) == 10 for p in packs[1:])

    blob = b''.join(records)
    lens = [len(r) for r in records]
    data = dict(
        # every record, concatenated, with the lengths beside them.  One
        # base64 string rather than 125 keeps the payload small.
        blob=base64.b64encode(blob).decode('ascii'),
        lens=lens,
        packs=packs,
        note=('side 2, 31 blocks = 31 packs; pack 0 (tape flag $80) holds '
              'dungeons 1-7, packs 1-30 (flag $C0) hold 10 sub-blocks each, '
              '8 and 9 being the treasure rooms.  Sub-block == record == one '
              'dungeon.  Decoded by the engine port of $97CB.'),
    )
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    print('%d slots -> %d distinct records, %d bytes (%d b64)' %
          (nslots, len(records), len(blob), len(data['blob'])))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
