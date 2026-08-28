#!/usr/bin/env python3
"""
battery.py -- the post-harness half of the section 8 diagnostic battery,
run against DRIVEN play (manual: "coverage is a lower bound and only as good
as the play that drove it").

  Q2  port inventory, dynamically
  Q3  self-modification: writes into regions that have been executed
  Q10 clock model: histogram of T-states between visits to the loop top
  Q13 code-vs-data from execution coverage
  Q15 render model: display-file read-back, bulk screen moves, blitter opcode
  Q16 BRIGHT / FLASH and where colour comes from
  Q17 speaker writes
  Q19 stack-imbalance scan: SP at every CALL vs at the matching RET

Usage:  python tools/battery.py [--state build/state_charsel.pkl] [--steps N]
"""
import os
import pickle
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, TAPE_CALL_PC, CPU_HZ   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402

FRAME_T = 69888
KEYMAP = {n: (s, b) for n, s, b in KEYS}

# an input script: (frames_to_hold, [key names])
SCRIPT = [
    (40, []), (60, ['D']), (60, ['Z']), (60, ['S']), (40, ['Q']),
    (60, ['1']), (60, ['D', 'Z']), (40, []), (60, ['S']), (60, ['1']),
]


class Probe:
    """One object doing all the watching, so a single driven run answers
    everything (manual 6.5: the same tracer is the port inventory, the
    sound rig and the border-effect detector)."""

    def __init__(self, h):
        self.h = h
        self.exec_bytes = bytearray(0x10000)   # Q13 coverage, every byte of every instruction
        self.writes_to_code = []               # Q3
        self.scr_reads = 0                     # Q15(b)
        self.scr_read_pcs = Counter()
        self.speaker = []                      # Q17 (T, value)
        self.attr_writes = Counter()           # Q16
        self.call_sp = []                      # Q19
        self.imbalance = Counter()
        self.loop_intervals = []
        self.ports_in = Counter()
        self.ports_out = Counter()


def instr_len(mem, pc):
    import z80dis.z80 as z
    try:
        d = z.decode(bytes(mem.m[pc:pc + 4]), pc)
        return d.len if d.len else 1
    except Exception:                          # noqa: BLE001
        return 1


def drive(h, probe, loop_pc, frames_total):
    """Run the input script, watching everything."""
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    ex = probe.exec_bytes
    last_loop_t = None

    # hook the ports object
    p = h.ports
    orig_read = p.read_port
    orig_write = p.write_port

    def read_port(registers, port):
        probe.ports_in[port & 0xFFFF] += 1
        return orig_read(registers, port)

    def write_port(registers, port, value):
        probe.ports_out[port & 0xFF] += 1
        if (port & 0xFF) == 0xFE:
            probe.speaker.append((registers[T], value & 0x10))
        return orig_write(registers, port, value)

    p.read_port = read_port
    p.write_port = write_port
    sim.set_tracer(p)

    # hook memory writes
    memobj = h.memobj
    memobj.watch(0x4000, 0x10000)

    end_t = regs[T] + frames_total * FRAME_T
    script = list(SCRIPT)
    seg_end = regs[T]
    while regs[T] < end_t:
        if regs[T] >= seg_end and script:
            frames, keys = script.pop(0)
            p.release_all()
            for k in keys:
                sel, bit = KEYMAP[k]
                p.press(sel, keymask(bit))
            seg_end = regs[T] + frames * FRAME_T
        pc = regs[PC]
        if pc == loop_pc:
            t = regs[T]
            if last_loop_t is not None:
                probe.loop_intervals.append(t - last_loop_t)
            last_loop_t = t
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        op = mem[pc]
        if op == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        # Q13: mark every byte of the instruction
        n = instr_len(memobj, pc)
        for i in range(n):
            ex[(pc + i) & 0xFFFF] = 1
        # Q19: record SP at CALLs
        if op in (0xCD, 0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC):
            probe.call_sp.append((pc, regs[SP]))
        ops[op]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)

    memobj.unwatch()
    p.read_port = orig_read
    p.write_port = orig_write
    sim.set_tracer(p)


def main():
    args = sys.argv[1:]
    state = os.path.join(ROOT, 'build', 'state_charsel.pkl')
    frames = 220
    loop_pc = 0x8503
    while args:
        if args[0] == '--state':
            state = args[1]; del args[:2]
        elif args[0] == '--frames':
            frames = int(args[1]); del args[:2]
        elif args[0] == '--loop':
            loop_pc = int(args[1], 0); del args[:2]
        else:
            del args[:1]

    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    probe = Probe(h)
    print(f'driving {frames} video frames from {os.path.basename(state)} ...')
    drive(h, probe, loop_pc, frames)
    mem = h.memobj

    print('\n== Q2  PORT INVENTORY (dynamic, driven play) ==')
    print('  reads :')
    for port, n in sorted(probe.ports_in.items(), key=lambda kv: -kv[1])[:12]:
        print(f'    ${port:04X}  x{n}')
    print('  writes (low byte):')
    for port, n in sorted(probe.ports_out.items(), key=lambda kv: -kv[1])[:12]:
        print(f'    ${port:02X}  x{n}')

    print('\n== Q3  SELF-MODIFICATION (writes into executed regions) ==')
    ex = probe.exec_bytes
    hits = Counter()
    for pc, addr, val in mem.log:
        if ex[addr]:
            hits[(pc, addr)] += 1
    print(f'  {len(mem.log)} writes logged; {len(hits)} distinct '
          f'(writer PC, target) pairs landed on executed bytes')
    for (pc, addr), n in hits.most_common(15):
        print(f'    ${pc:04X} writes ${addr:04X}  x{n}')

    print('\n== Q10 CLOCK MODEL ==')
    xs = probe.loop_intervals
    if not xs:
        print(f'  loop top ${loop_pc:04X} never visited')
    else:
        c = Counter(round(x / FRAME_T, 2) for x in xs)
        print(f'  {len(xs)} intervals, mean {sum(xs)/len(xs):.0f} T '
              f'= {sum(xs)/len(xs)/FRAME_T:.3f} frames')
        for k in sorted(c):
            print(f'    {k:6.2f} frames  {"#"*min(50,c[k])} {c[k]}')

    print('\n== Q13 EXECUTION COVERAGE ==')
    total = sum(ex)
    print(f'  {total} bytes executed')
    runs = []
    s = None
    for a in range(0x10000):
        if ex[a] and s is None:
            s = a
        elif not ex[a] and s is not None:
            runs.append((s, a - 1)); s = None
    if s is not None:
        runs.append((s, 0xFFFF))
    big = [r for r in runs if r[1] - r[0] >= 24]
    print(f'  {len(runs)} runs, {len(big)} of them >= 25 bytes:')
    for a, b in big[:40]:
        print(f'    ${a:04X}-${b:04X}  ({b-a+1})')

    print('\n== Q15 RENDER MODEL ==')
    scr_w = [w for w in mem.log if 0x4000 <= w[1] < 0x5800]
    attr_w = [w for w in mem.log if 0x5800 <= w[1] < 0x5B00]
    print(f'  display-file writes: {len(scr_w)}   attribute writes: {len(attr_w)}')
    wp = Counter(w[0] for w in scr_w)
    print('  top display-file writer PCs:')
    for pc, n in wp.most_common(10):
        print(f'    ${pc:04X}  x{n}')
    ap = Counter(w[0] for w in attr_w)
    print('  top attribute writer PCs:')
    for pc, n in ap.most_common(8):
        print(f'    ${pc:04X}  x{n}')

    print('\n== Q16 BRIGHT / FLASH ==')
    vals = Counter(w[2] for w in attr_w)
    bright = sum(n for v, n in vals.items() if v & 0x40)
    flash = sum(n for v, n in vals.items() if v & 0x80)
    print(f'  distinct attribute values written: {len(vals)}')
    print(f'  with BRIGHT set: {bright} of {len(attr_w)}   with FLASH set: {flash}')
    print('  values: ' + ' '.join(f'${v:02X}x{n}' for v, n in vals.most_common(20)))

    print('\n== Q17 SPEAKER ==')
    sp = probe.speaker
    print(f'  writes to $FE: {len(sp)}')
    if sp:
        edges = [sp[i] for i in range(1, len(sp)) if sp[i][1] != sp[i - 1][1]]
        print(f'  speaker-bit changes: {len(edges)}')
        if len(edges) > 4:
            gaps = [edges[i][0] - edges[i - 1][0] for i in range(1, min(60, len(edges)))]
            print(f'  first gaps (T): {gaps[:20]}')

    print('\n== Q19 STACK IMBALANCE (calls seen) ==')
    print(f'  {len(probe.call_sp)} CALLs recorded (matching-RET pass needs the '
          f'per-call trace; see notes)')


if __name__ == '__main__':
    main()
