#!/usr/bin/env python3
"""
walk.py -- recursive-descent code walker over the 64K image.

Manual 3.9: "a binary interleaves code and data, and any byte sequence you
search for will also occur inside operands, tables and graphics.  Confirm EVERY
hit by disassembling from a known instruction boundary."  This walker produces
those known boundaries: starting from the entry point it follows every
statically-resolvable transfer of control and records the address of every
instruction byte it decodes.

It is a LOWER BOUND on code (indirect jumps, jump tables and data-driven
dispatch are not followed) and it is complemented later by execution coverage
from the simulator (Q13).  Anything it reaches is definitely code; anything it
does not reach is unknown, not data.

Usage:
    python tools/walk.py [--entry 0x8400] [--entry ...] [--image F]
                         [--report opcodes|regions|calls]
"""
import json
import os
import sys

import z80dis.z80 as z

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# opcodes after which control does not fall through
STOP = {0xC9, 0xC3, 0xE9, 0x18, 0x76}          # RET, JP nn, JP (HL), JR, HALT
# conditional returns still fall through
COND_RET = {0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8}


def walk(mem, entries, limit=0x10000):
    seen = set()          # addresses that begin an instruction
    covered = set()       # every byte belonging to a decoded instruction
    calls = {}            # target -> set of call sites
    jumps = {}
    pending = list(entries)
    while pending:
        pc = pending.pop()
        while True:
            if pc in seen or pc >= limit:
                break
            try:
                d = z.decode(bytes(mem[pc:pc + 4]), pc)
            except Exception:                       # noqa: BLE001
                break
            if d.status != z.DECODE_STATUS.OK:
                break
            seen.add(pc)
            for i in range(d.len):
                covered.add(pc + i)
            op = mem[pc]
            nxt = pc + d.len

            tgt = None
            if op in (0xC3,):                                   # JP nn
                tgt = mem[pc + 1] | (mem[pc + 2] << 8)
            elif op in (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA):  # JP cc,nn
                tgt = mem[pc + 1] | (mem[pc + 2] << 8)
            elif op == 0x18:                                    # JR e
                tgt = (nxt + ((mem[pc + 1] ^ 0x80) - 0x80)) & 0xFFFF
            elif op in (0x20, 0x28, 0x30, 0x38, 0x10):          # JR cc,e / DJNZ
                tgt = (nxt + ((mem[pc + 1] ^ 0x80) - 0x80)) & 0xFFFF
            elif op == 0xCD or op in (0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC):
                tgt = mem[pc + 1] | (mem[pc + 2] << 8)
                calls.setdefault(tgt, set()).add(pc)
                pending.append(tgt)
                tgt = None
            elif op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):   # RST
                calls.setdefault(op & 0x38, set()).add(pc)

            if tgt is not None:
                jumps.setdefault(tgt, set()).add(pc)
                if op in (0xC3, 0x18):
                    pc = tgt
                    continue
                pending.append(tgt)

            if op in STOP:
                break
            pc = nxt
    return seen, covered, calls, jumps


def regions(covered):
    out = []
    s = None
    prev = None
    for a in sorted(covered):
        if s is None:
            s = prev = a
            continue
        if a == prev + 1:
            prev = a
            continue
        out.append((s, prev))
        s = prev = a
    if s is not None:
        out.append((s, prev))
    return out


# ---------------------------------------------------------------------------
# opcode inventory over confirmed instruction boundaries
# ---------------------------------------------------------------------------

def inventory(mem, seen):
    ports_out, ports_in, rom_calls, halts, ims = [], [], [], [], []
    for pc in sorted(seen):
        op = mem[pc]
        if op == 0xD3:
            ports_out.append((pc, mem[pc + 1], 'OUT (n),A'))
        elif op == 0xDB:
            ports_in.append((pc, mem[pc + 1], 'IN A,(n)'))
        elif op == 0xED:
            op2 = mem[pc + 1]
            if (op2 & 0xC7) == 0x41:
                ports_out.append((pc, None, f'OUT (C),{z.decoded2str(z.decode(bytes(mem[pc:pc+4]), pc))}'))
            elif (op2 & 0xC7) == 0x40:
                ports_in.append((pc, None, f'IN r,(C)'))
            elif op2 in (0x46, 0x56, 0x5E, 0x4E, 0x66, 0x6E, 0x76, 0x7E):
                ims.append((pc, z.decoded2str(z.decode(bytes(mem[pc:pc + 4]), pc))))
        elif op == 0x76:
            halts.append(pc)
        elif op in (0xCD, 0xC3, 0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC,
                    0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA):
            t = mem[pc + 1] | (mem[pc + 2] << 8)
            if t < 0x4000:
                rom_calls.append((pc, t))
        elif op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
            rom_calls.append((pc, op & 0x38))
    return ports_out, ports_in, rom_calls, halts, ims


if __name__ == '__main__':
    args = sys.argv[1:]
    img = os.path.join(ROOT, 'build', 'image.bin')
    entries = []
    report = 'all'
    while args:
        if args[0] == '--image':
            img = args[1]; del args[:2]
        elif args[0] == '--entry':
            entries.append(int(args[1], 0)); del args[:2]
        elif args[0] == '--report':
            report = args[1]; del args[:2]
        else:
            del args[:1]
    if not entries:
        entries = [0x8400]
    mem = bytearray(open(img, 'rb').read())
    seen, covered, calls, jumps = walk(mem, entries)
    print(f'entries: {[hex(e) for e in entries]}')
    print(f'instructions reached: {len(seen)}   bytes covered: {len(covered)}')

    if report in ('all', 'regions'):
        rs = regions(covered)
        print(f'\n-- {len(rs)} contiguous code regions --')
        for s, e in rs:
            if e - s >= 3:
                print(f'  ${s:04X}-${e:04X}  ({e - s + 1})')

    po, pi, rc, hl, ims = inventory(mem, seen)
    if report in ('all', 'opcodes'):
        print(f'\n-- PORT WRITES ({len(po)}) --')
        for pc, p, t in po:
            print(f'  ${pc:04X}  {t}' + (f'   port ${p:02X}' if p is not None else ''))
        print(f'\n-- PORT READS ({len(pi)}) --')
        for pc, p, t in pi:
            print(f'  ${pc:04X}  {t}' + (f'   port ${p:02X}' if p is not None else ''))
        print(f'\n-- TRANSFERS BELOW $4000 ({len(rc)}) --')
        for pc, t in rc:
            print(f'  ${pc:04X}  -> ${t:04X}')
        print(f'\n-- HALT ({len(hl)}) --')
        for pc in hl:
            print(f'  ${pc:04X}')
        print(f'\n-- IM ({len(ims)}) --')
        for pc, t in ims:
            print(f'  ${pc:04X}  {t}')

    if report in ('all', 'calls'):
        print(f'\n-- {len(calls)} distinct call targets --')
        for t in sorted(calls):
            sites = sorted(calls[t])
            print(f'  ${t:04X}  x{len(sites)}  from ' +
                  ' '.join(f'${s:04X}' for s in sites[:8]) +
                  (' ...' if len(sites) > 8 else ''))

    json.dump({'seen': sorted(seen), 'covered': sorted(covered)},
              open(os.path.join(ROOT, 'build', 'walk.json'), 'w'))
