#!/usr/bin/env python3
"""
tzx.py -- a TZX container parser written against the TZX format spec.

Phase 1 of the porting pipeline (PORTING-ZX-TO-JS.txt).

Design rules taken from the manual:
  - PRINT AND STOP on any block ID we do not handle. Never skip: a skipped
    0x12/0x13 pilot pair makes a following 0x14 look freestanding and gives a
    byte-shifted image that disassembles into plausible nonsense.
  - Handle the flow-control blocks (0x24/0x25 loop, 0x21/0x22 group, 0x23 jump,
    0x26/0x27 call, 0x28 select) explicitly, because ignoring them emits the
    wrong NUMBER of data blocks, silently.
  - Verify every ROM-format checksum (XOR fold over flag + body vs last byte).
  - Report, per data block, whether it looks like a ROM-format block at all.

Usage:
    python tools/tzx.py <file.tzx> [--dump OUTDIR]
"""

import sys
import os
import struct

# ---------------------------------------------------------------------------
# block table: id -> (name, header_size_or_callable)
# 'header_size' is the number of bytes AFTER the id byte that are fixed fields.
# Blocks with variable payloads get a callable returning total size after id.
# ---------------------------------------------------------------------------


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _u24(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


class Block:
    def __init__(self, index, offset, bid, name):
        self.index = index
        self.offset = offset
        self.id = bid
        self.name = name
        self.data = None          # payload bytes for data-carrying blocks
        self.info = {}            # decoded fields
        self.checksum_ok = None   # None = not a ROM-format block

    def __repr__(self):
        return f'<Block #{self.index} id=0x{self.id:02X} {self.name} len={0 if self.data is None else len(self.data)}>'


class TzxError(Exception):
    pass


def parse(path):
    with open(path, 'rb') as f:
        b = f.read()

    if b[:8] != b'ZXTape!\x1a':
        raise TzxError(f'{path}: not a TZX (bad signature {b[:8]!r})')
    major, minor = b[8], b[9]

    blocks = []
    o = 10
    idx = 0
    while o < len(b):
        bid = b[o]
        start = o
        o += 1

        if bid == 0x10:                     # Standard speed data block
            pause = _u16(b, o)
            ln = _u16(b, o + 2)
            data = b[o + 4:o + 4 + ln]
            o += 4 + ln
            blk = Block(idx, start, bid, 'Standard Speed Data')
            blk.data = data
            blk.info = {'pause_ms': pause}
            blocks.append(blk)

        elif bid == 0x11:                   # Turbo speed data block
            pilot = _u16(b, o)
            sync1 = _u16(b, o + 2)
            sync2 = _u16(b, o + 4)
            zero = _u16(b, o + 6)
            one = _u16(b, o + 8)
            pilot_len = _u16(b, o + 10)
            used_bits = b[o + 12]
            pause = _u16(b, o + 13)
            ln = _u24(b, o + 15)
            data = b[o + 18:o + 18 + ln]
            o += 18 + ln
            blk = Block(idx, start, bid, 'Turbo Speed Data')
            blk.data = data
            blk.info = {'pilot': pilot, 'sync1': sync1, 'sync2': sync2,
                        'zero': zero, 'one': one, 'pilot_pulses': pilot_len,
                        'used_bits': used_bits, 'pause_ms': pause}
            blocks.append(blk)

        elif bid == 0x12:                   # Pure tone
            blk = Block(idx, start, bid, 'Pure Tone')
            blk.info = {'pulse_len': _u16(b, o), 'pulses': _u16(b, o + 2)}
            o += 4
            blocks.append(blk)

        elif bid == 0x13:                   # Pulse sequence
            n = b[o]
            blk = Block(idx, start, bid, 'Pulse Sequence')
            blk.info = {'pulses': [_u16(b, o + 1 + 2 * i) for i in range(n)]}
            o += 1 + 2 * n
            blocks.append(blk)

        elif bid == 0x14:                   # Pure data block
            zero = _u16(b, o)
            one = _u16(b, o + 2)
            used_bits = b[o + 4]
            pause = _u16(b, o + 5)
            ln = _u24(b, o + 7)
            data = b[o + 10:o + 10 + ln]
            o += 10 + ln
            blk = Block(idx, start, bid, 'Pure Data')
            blk.data = data
            blk.info = {'zero': zero, 'one': one, 'used_bits': used_bits,
                        'pause_ms': pause}
            blocks.append(blk)

        elif bid == 0x15:                   # Direct recording
            ln = _u24(b, o + 5)
            blk = Block(idx, start, bid, 'Direct Recording (DEFEATS BLOCK PARSER)')
            blk.info = {'tstates_per_sample': _u16(b, o), 'pause_ms': _u16(b, o + 2),
                        'used_bits': b[o + 4], 'bytes': ln}
            o += 8 + ln
            blocks.append(blk)

        elif bid == 0x18:                   # CSW recording
            ln = _u32(b, o)
            blk = Block(idx, start, bid, 'CSW Recording (DEFEATS BLOCK PARSER)')
            o += 4 + ln
            blocks.append(blk)

        elif bid == 0x19:                   # Generalized data block
            ln = _u32(b, o)
            blk = Block(idx, start, bid, 'Generalized Data (DEFEATS BLOCK PARSER)')
            o += 4 + ln
            blocks.append(blk)

        elif bid == 0x20:                   # Pause / stop the tape
            blk = Block(idx, start, bid, 'Pause/Stop')
            blk.info = {'pause_ms': _u16(b, o)}
            o += 2
            blocks.append(blk)

        elif bid == 0x21:                   # Group start
            n = b[o]
            blk = Block(idx, start, bid, 'Group Start')
            blk.info = {'text': b[o + 1:o + 1 + n].decode('latin-1')}
            o += 1 + n
            blocks.append(blk)

        elif bid == 0x22:                   # Group end
            blocks.append(Block(idx, start, bid, 'Group End'))

        elif bid == 0x23:                   # Jump to block
            blk = Block(idx, start, bid, 'Jump To Block')
            rel = _u16(b, o)
            blk.info = {'relative': rel - 65536 if rel > 32767 else rel}
            o += 2
            blocks.append(blk)

        elif bid == 0x24:                   # Loop start
            blk = Block(idx, start, bid, 'Loop Start')
            blk.info = {'repetitions': _u16(b, o)}
            o += 2
            blocks.append(blk)

        elif bid == 0x25:                   # Loop end
            blocks.append(Block(idx, start, bid, 'Loop End'))

        elif bid == 0x26:                   # Call sequence
            n = _u16(b, o)
            blk = Block(idx, start, bid, 'Call Sequence')
            blk.info = {'count': n}
            o += 2 + 2 * n
            blocks.append(blk)

        elif bid == 0x27:                   # Return from sequence
            blocks.append(Block(idx, start, bid, 'Return From Sequence'))

        elif bid == 0x28:                   # Select block
            ln = _u16(b, o)
            blk = Block(idx, start, bid, 'Select Block')
            o += 2 + ln
            blocks.append(blk)

        elif bid == 0x2A:                   # Stop the tape if in 48K mode
            ln = _u32(b, o)
            blk = Block(idx, start, bid, 'Stop Tape If 48K')
            o += 4 + ln
            blocks.append(blk)

        elif bid == 0x2B:                   # Set signal level
            ln = _u32(b, o)
            blk = Block(idx, start, bid, 'Set Signal Level')
            blk.info = {'level': b[o + 4] if ln >= 1 else None}
            o += 4 + ln
            blocks.append(blk)

        elif bid == 0x30:                   # Text description
            n = b[o]
            blk = Block(idx, start, bid, 'Text Description')
            blk.info = {'text': b[o + 1:o + 1 + n].decode('latin-1')}
            o += 1 + n
            blocks.append(blk)

        elif bid == 0x31:                   # Message block
            n = b[o + 1]
            blk = Block(idx, start, bid, 'Message')
            blk.info = {'time_s': b[o], 'text': b[o + 2:o + 2 + n].decode('latin-1')}
            o += 2 + n
            blocks.append(blk)

        elif bid == 0x32:                   # Archive info
            ln = _u16(b, o)
            body = b[o + 2:o + 2 + ln]
            n = body[0]
            items = []
            p = 1
            for _ in range(n):
                tid = body[p]
                sl = body[p + 1]
                items.append((tid, body[p + 2:p + 2 + sl].decode('latin-1')))
                p += 2 + sl
            blk = Block(idx, start, bid, 'Archive Info')
            blk.info = {'items': items}
            o += 2 + ln
            blocks.append(blk)

        elif bid == 0x33:                   # Hardware type
            n = b[o]
            blk = Block(idx, start, bid, 'Hardware Type')
            blk.info = {'entries': [(b[o + 1 + 3 * i], b[o + 2 + 3 * i], b[o + 3 + 3 * i])
                                    for i in range(n)]}
            o += 1 + 3 * n
            blocks.append(blk)

        elif bid == 0x35:                   # Custom info block
            ident = b[o:o + 10].decode('latin-1')
            ln = _u32(b, o + 10)
            blk = Block(idx, start, bid, 'Custom Info')
            blk.info = {'id': ident, 'len': ln}
            o += 14 + ln
            blocks.append(blk)

        elif bid == 0x5A:                   # Glue block
            blk = Block(idx, start, bid, 'Glue Block')
            o += 9
            blocks.append(blk)

        else:
            raise TzxError(
                f'{path}: UNHANDLED BLOCK ID 0x{bid:02X} at file offset '
                f'0x{start:X} (block #{idx}). Stopping rather than guessing.')

        idx += 1

    # checksum the ROM-format data blocks
    for blk in blocks:
        if blk.data is not None and len(blk.data) >= 2:
            x = 0
            for byte in blk.data[:-1]:
                x ^= byte
            blk.checksum_ok = (x == blk.data[-1])

    return (major, minor), blocks


# ---------------------------------------------------------------------------
# ROM header decoding (flag 0x00, 19 bytes: flag + 17 + checksum)
# ---------------------------------------------------------------------------

HDR_TYPES = {0: 'BASIC program', 1: 'Number array', 2: 'Character array', 3: 'Code'}


def decode_header(data):
    if len(data) != 19 or data[0] != 0x00:
        return None
    t = data[1]
    name = data[2:12].decode('latin-1')
    length = _u16(data, 12)
    p1 = _u16(data, 14)
    p2 = _u16(data, 16)
    d = {'type': t, 'type_name': HDR_TYPES.get(t, f'?{t}'), 'name': name,
         'length': length, 'param1': p1, 'param2': p2}
    if t == 0:
        d['autostart_line'] = p1 if p1 < 32768 else None
        d['vars_offset'] = p2
    elif t == 3:
        d['load_address'] = p1
    return d


def report(path, dump_dir=None):
    (maj, mino), blocks = parse(path)
    print('=' * 78)
    print(f'{os.path.basename(path)}   TZX v{maj}.{mino}   {os.path.getsize(path)} bytes')
    print('=' * 78)

    pending_header = None
    data_idx = 0
    problems = []

    for blk in blocks:
        line = f'#{blk.index:3d} @0x{blk.offset:06X}  id=0x{blk.id:02X}  {blk.name}'
        print(line)
        for k, v in blk.info.items():
            print(f'          {k} = {v!r}')
        if blk.data is not None:
            flag = blk.data[0] if blk.data else None
            print(f'          bytes = {len(blk.data)}   flag = '
                  f'{"0x%02X" % flag if flag is not None else "-"}   '
                  f'checksum {"OK" if blk.checksum_ok else "*** BAD ***"}')
            if not blk.checksum_ok:
                problems.append(f'block #{blk.index}: bad checksum')
            hdr = decode_header(blk.data)
            if hdr:
                print(f'          HEADER: {hdr["type_name"]:15s} name={hdr["name"]!r} '
                      f'len={hdr["length"]} p1={hdr["param1"]} (0x{hdr["param1"]:04X}) '
                      f'p2={hdr["param2"]} (0x{hdr["param2"]:04X})')
                pending_header = hdr
            else:
                if pending_header is not None:
                    body = len(blk.data) - 2      # minus flag and checksum
                    ok = (body == pending_header['length'])
                    print(f'          body={body} vs header len={pending_header["length"]}'
                          f'  {"MATCH" if ok else "*** MISMATCH ***"}')
                    if not ok:
                        problems.append(
                            f'block #{blk.index}: body {body} != header len '
                            f'{pending_header["length"]}')
                    pending_header = None
            if dump_dir:
                os.makedirs(dump_dir, exist_ok=True)
                base = os.path.splitext(os.path.basename(path))[0].replace(' ', '_')
                fn = os.path.join(dump_dir, f'{base}.b{blk.index:02d}.bin')
                with open(fn, 'wb') as f:
                    f.write(blk.data)
                # also dump the payload with flag/checksum stripped
                if len(blk.data) >= 2:
                    fn2 = os.path.join(dump_dir, f'{base}.b{blk.index:02d}.body.bin')
                    with open(fn2, 'wb') as f:
                        f.write(blk.data[1:-1])
            data_idx += 1

    print('-' * 78)
    print(f'{len(blocks)} blocks, {data_idx} carrying data.')
    if problems:
        print('PROBLEMS:')
        for p in problems:
            print('  ' + p)
    else:
        print('No checksum or length problems.')
    return blocks


if __name__ == '__main__':
    args = sys.argv[1:]
    dump = None
    if '--dump' in args:
        i = args.index('--dump')
        dump = args[i + 1]
        del args[i:i + 2]
    for p in args:
        report(p, dump)
        print()
