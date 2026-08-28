#!/usr/bin/env python3
"""
harness.py -- the Z80 simulator harness.  Phase 7 of PORTING-ZX-TO-JS.txt.
THIS IS THE PIVOT: nothing before it is verifiable and nothing after it is
guesswork.

=============================================================================
WHAT THIS SUBSTITUTE ENVIRONMENT DOES AND DOES NOT PRESERVE   (manual E2/6.2)
=============================================================================

ROM.  A REAL 48K ROM IMAGE IS USED, read from the SkoolKit installation's
      resources/48.rom on this machine.  It is used LOCALLY ONLY: it is not
      copied into the repository, not embedded in the artifact, and its
      behaviour is not transcribed into the port.  It is used because the game
      calls ROM code at runtime (the level loader enters LD-BYTES at $056B),
      and a dummy ROM would make those measurements fiction.
      Writes below $4000 are IGNORED, as on real hardware.

TAPE. There is no tape signal model.  The tape deck below intercepts the
      game's entry into the ROM loader at a PC BREAKPOINT and performs the
      load by fiat: it copies the next matching tape block into memory and
      returns with the flags LD-BYTES would have set.  Consequences:
        - the T-states a real load would take are NOT charged.  No gameplay
          measurement depends on them.
        - loading errors and the game's retry paths are not exercised.

PORTS.  Unattached ports return $FF.  Kempston ($1F) returns $00 (active
      high) so nothing is held.  DECLARED, not measured: if the game draws
      entropy from the floating bus this makes it deterministic in a way the
      real machine is not.  Q2/Q18 settle that.

TIMING. Unaffected by the ROM and port substitutions above: a substituted
      byte changes what is read, not how long the read takes.  Memory
      contention is NOT modelled (plain Simulator, not CMIOSimulator); see
      3.8 -- ratios and differential tests are unaffected, absolute rates for
      code below $8000 are a lower bound.
=============================================================================
"""
import os
import sys

from skoolkit.simulator import Simulator
from skoolkit import simutils as U

# --- ULA CONTENTION --------------------------------------------------------
# The 48K contends $4000..$7FFF while the display is being read, and this game
# keeps BOTH the display file and its own data tables ($73DA..$7F1A -- the
# sprite pointer table, the step table, the damage tables, the strings) in
# that range.  A plain Simulator charges none of it.  THIS HARNESS CONTENDS
# BY DEFAULT, because the engine's pass-cost model now does.
#
# MEASURED, on the captured state: contention costs about 7,400 T a pass --
# 6,555 T of it in the screen blit ($9CD8's three copies into $4000/$5800)
# and only ~840 T inside the game's own routines, which is a ninth of a video
# frame.  The $A29F interrupt body does not move at all; it lives above
# $8000.  But the game sits WITHIN that of a frame boundary, so the small
# cost tips nearly every pass from four frames to five: 11.3 Hz uncontended
# against 10.0 Hz contended, and 10.0 Hz is what real hardware does.
#
# WHAT IT DOES NOT CHANGE: every screen and table capture in build/ is
# byte-identical either way (verified on introgate), and every logic gate
# substitutes the original's own $8497 into the engine, so potiongate and
# p2gate report 0 mismatching rows under both.  Only the CLOCK moves.
#
# Set GAUNTLET_CONTENDED=0 for the old uncontended baseline.  Scoring the
# model against the wrong one is meaningless in either direction: matched,
# `clockgate.py score` is 98.6% contended and 92.8% plain; crossed, it is
# 77.6% and 81.7%.
CONTENDED = os.environ.get('GAUNTLET_CONTENDED', '1') not in ('', '0')
if CONTENDED:
    from skoolkit.cmiosimulator import CMIOSimulator as _SimClass
else:
    _SimClass = Simulator

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import tzx as tzxmod                                     # noqa: E402

# --- SkoolKit register indices (write them down: manual 6.4) ----------------
A, F, B, C, D, E, H, L = 0, 1, 2, 3, 4, 5, 6, 7
IXh, IXl, IYh, IYl = 8, 9, 10, 11
SP, I, R, PC, T, IFF, IM, HALT = 12, 14, 15, 24, 25, 26, 27, 28
xA, xF = 16, 17

CPU_HZ = 3_500_000
FRAME_T = 69888                        # 48K frame duration

# --- the loader's postcondition, recovered in phase 2 ----------------------
BLOCK_A = (0x8600, 0x5210, 0x80)       # dest, length, tape flag
BLOCK_B = (0x73DA, 0x0B41, 0x81)
BLOCK_C = (0x8400, 0x7777, 0x82)
ENTRY = 0x8400
STUB_SRC = 0x5CF1
STUB_DST = 0xFF00
STUB_LEN = 0x100
PROG = 0x5CCB

SIDE1 = os.path.join(ROOT, 'tape', 'Gauntlet - Side 1.tzx')
SIDE2 = os.path.join(ROOT, 'tape', 'Gauntlet - Side 2.tzx')

# The game's runtime level loader (phase 2):
#   $9247  CALL $056B  -- entry into the ROM's LD-BYTES after the game has set
#   IX (dest), DE (length), A' (expected flag) and carry' (load not verify).
TAPE_CALL_PC = 0x9247
TAPE_RET_PC = 0x924A


def rom48():
    return bytearray(open(U.ROM48, 'rb').read())


def tape_blocks(path):
    """Data blocks off a tape, in order, as (flag, payload) pairs."""
    _, blocks = tzxmod.parse(path)
    return [(b.data[0], b.data[1:-1]) for b in blocks
            if b.data is not None and len(b.data) >= 2]


class Mem:
    """64K memory: ROM read-only below $4000, optional write log."""

    __slots__ = ('m', 'sim', 'log', 'watch_lo', 'watch_hi', 'watching', 'rom_writes')

    def __init__(self, data):
        self.m = data
        self.sim = None
        self.log = []
        self.watch_lo = 0
        self.watch_hi = 0
        self.watching = False
        self.rom_writes = 0

    def __len__(self):
        return 0x10000

    def __getitem__(self, i):
        return self.m[i]

    def __setitem__(self, i, v):
        if i < 0x4000:
            self.rom_writes += 1
            return
        if self.watching and self.watch_lo <= i < self.watch_hi:
            self.log.append((self.sim.registers[PC] if self.sim else -1, i, v))
        self.m[i] = v

    def __iter__(self):
        return iter(self.m)

    def watch(self, lo=0x4000, hi=0x10000):
        self.watch_lo, self.watch_hi, self.watching = lo, hi, True
        self.log = []

    def unwatch(self):
        self.watching = False


class Ports:
    """Input and measurement in one object (manual 6.5).

    `pressed` maps a HALF-ROW SELECT byte -- the value the game puts in the
    high address byte, with ONE BIT CLEAR -- to the value returned for it.  To
    press a key you store the game's own mask VERBATIM: the key's bit is the
    ZERO in the mask (3.4 / G2).
    """

    def __init__(self):
        self.pressed = {}
        self.kempston = 0x00       # ACTIVE HIGH; 0 = nothing pressed
        self.writes = []
        self.record_writes = False
        self.reads = {}
        self.unattached = 0xFF

    def press(self, select, value):
        self.pressed[select] = self.pressed.get(select, 0xFF) & value

    def release_all(self):
        self.pressed.clear()
        self.kempston = 0x00

    def read_port(self, registers, port):
        self.reads[port] = self.reads.get(port, 0) + 1
        low = port & 0xFF
        if low & 1 == 0:                        # the ULA answers every even port
            hi = (port >> 8) & 0xFF
            v = 0xFF
            if hi == 0:                         # B=0 selects ALL eight half-rows
                for val in self.pressed.values():
                    v &= val
            else:
                for select, val in self.pressed.items():
                    # select has one clear bit; the half-row is selected when
                    # that bit is also clear in the high address byte
                    if (~hi & ~select) & 0xFF:
                        v &= val
            return v
        if low == 0x1F:                         # Kempston, ACTIVE HIGH
            return self.kempston
        return self.unattached

    def write_port(self, registers, port, value):
        if self.record_writes:
            self.writes.append((registers[T], port, value))


class TapeDeck:
    """Serves tape blocks at a PC breakpoint instead of modelling the signal.

    On entry the game has set IX = destination, DE = length, A' = expected
    flag, carry' = set (load).  We honour the flag match and the length and
    set carry on success exactly as LD-BYTES does.
    """

    def __init__(self, blocks, name=''):
        self.blocks = blocks
        self.name = name
        self.pos = 0
        self.log = []

    def rewind(self):
        self.pos = 0

    def serve(self, sim):
        regs = sim.registers
        mem = sim.memory
        ix = regs[IXl] + 256 * regs[IXh]
        de = regs[E] + 256 * regs[D]
        want = regs[xA]
        i = self.pos
        while i < len(self.blocks) and self.blocks[i][0] != want:
            i += 1
        if i >= len(self.blocks):
            self.log.append(('NOMATCH', want, ix, de, self.pos))
            regs[F] &= 0xFE
            regs[PC] = TAPE_RET_PC
            return False
        flag, payload = self.blocks[i]
        self.pos = i + 1
        n = min(de, len(payload))
        for k in range(n):
            mem[(ix + k) & 0xFFFF] = payload[k]
        self.log.append(('LOAD', i, flag, ix, de, len(payload), n))
        regs[F] |= 0x01
        regs[IXh] = ((ix + n) >> 8) & 0xFF
        regs[IXl] = (ix + n) & 0xFF
        regs[D] = ((de - n) >> 8) & 0xFF
        regs[E] = (de - n) & 0xFF
        regs[PC] = TAPE_RET_PC
        return True


class Harness:
    def __init__(self, use_rom=True, tape=SIDE2, deck=True):
        raw = bytearray(0x10000)
        if use_rom:
            raw[0:0x4000] = rom48()
        else:
            for i in range(256):
                raw[i] = (i * 37 + 11) & 0xFF
        self.use_rom = use_rom

        blocks = tape_blocks(SIDE1)
        by_flag = {}
        for flag, payload in blocks:
            by_flag.setdefault(flag, []).append(payload)

        for dest, ln, flag in (BLOCK_A, BLOCK_B, BLOCK_C):
            payload = by_flag[flag][0]
            assert len(payload) == ln, f'flag ${flag:02X}: {len(payload)} != ${ln:04X}'
            raw[dest:dest + ln] = payload
        basic = by_flag[0xFF][0]
        stub = basic[STUB_SRC - PROG: STUB_SRC - PROG + STUB_LEN]
        raw[STUB_DST:STUB_DST + STUB_LEN] = stub

        self.memobj = Mem(raw)
        self.ports = Ports()
        self.sim = _SimClass(self.memobj,
                             config={'fast_djnz': True, 'fast_ldir': True})
        self.memobj.sim = self.sim
        self.sim.set_tracer(self.ports)
        self.deck = TapeDeck(tape_blocks(tape), os.path.basename(tape)) if (deck and tape) else None
        self.tape_stops = 0
        self.halts_skipped = 0
        self.frame_duration = self.sim.frame_duration
        self.int_active = self.sim.int_active
        r = self.sim.registers
        r[PC] = ENTRY
        r[SP] = 0xFFFB
        r[IFF] = 0
        r[IM] = 1

    # -- accessors ----------------------------------------------------------
    @property
    def regs(self):
        return self.sim.registers

    @property
    def mem(self):
        return self.memobj

    def reg16(self, hi, lo):
        return self.sim.registers[lo] + 256 * self.sim.registers[hi]

    def hl(self):
        return self.reg16(H, L)

    def de(self):
        return self.reg16(D, E)

    def bc(self):
        return self.reg16(B, C)

    def ix(self):
        return self.reg16(IXh, IXl)

    def iy(self):
        return self.reg16(IYh, IYl)

    def pc(self):
        return self.sim.registers[PC]

    def tstates(self):
        return self.sim.registers[T]

    def peek(self, a, n=1):
        return bytes(self.memobj.m[a:a + n])

    def peek16(self, a):
        return self.memobj.m[a] | (self.memobj.m[a + 1] << 8)

    def poke(self, a, *vals):
        for i, v in enumerate(vals):
            self.memobj.m[a + i] = v & 0xFF

    # -- stepping -----------------------------------------------------------
    def _tape(self):
        self.deck.serve(self.sim)
        self.tape_stops += 1

    def _fast_halt(self):
        """A HALT costs 4 T-states and re-executes until the frame interrupt,
        which is ~17,000 simulated instructions per frame -- unaffordable, and
        this game HALTs twice per pass.  Jump the clock to the interrupt window
        instead, advancing R by the number of refreshes skipped so that a game
        reading LD A,R still sees the right value."""
        regs = self.sim.registers
        fd, ia = self.frame_duration, self.int_active
        pc = regs[PC]
        t = regs[T]
        if t % fd >= ia:
            nt = (t // fd + 1) * fd
            skipped = (nt - t) // 4
            regs[T] = nt
            regs[R] = (regs[R] & 0x80) | ((regs[R] + skipped) & 0x7F)
            self.halts_skipped += skipped
        regs[PC] = (pc + 1) & 0xFFFF
        regs[HALT] = 0
        self.sim.accept_interrupt(regs, self.sim.memory, pc)

    def step(self, n=1, interrupts=True):
        sim = self.sim
        regs = sim.registers
        opcodes = sim.opcodes
        mem = sim.memory
        fd, ia = self.frame_duration, self.int_active
        for _ in range(n):
            pc = regs[PC]
            if self.deck is not None and pc == TAPE_CALL_PC:
                self._tape()
                continue
            if interrupts and mem[pc] == 0x76 and regs[IFF]:
                self._fast_halt()
                continue
            opcodes[mem[pc]]()
            if interrupts and regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)

    def run_until(self, targets=(), limit=5_000_000, stride=4096,
                  interrupts=True, predicate=None):
        """Run until PC hits any of `targets`, or `predicate(self)` is true
        (checked every `stride` instructions).  Returns (reason, steps)."""
        if isinstance(targets, int):
            targets = (targets,)
        targets = set(targets)
        sim = self.sim
        regs = sim.registers
        opcodes = sim.opcodes
        mem = sim.memory
        deck = self.deck
        fd, ia = self.frame_duration, self.int_active
        n = 0
        while n < limit:
            pc = regs[PC]
            if pc in targets:
                return ('target', n)
            if deck is not None and pc == TAPE_CALL_PC:
                self._tape()
                n += 1
                continue
            if interrupts and mem[pc] == 0x76 and regs[IFF]:
                self._fast_halt()
                n += 1
                continue
            opcodes[mem[pc]]()
            if interrupts and regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            n += 1
            if predicate is not None and n % stride == 0 and predicate(self):
                return ('predicate', n)
        return ('limit', n)

    def profile(self, n=200_000, interrupts=True, bucket_isr=None):
        """Histogram the PC over n instructions (manual 6.4).  If bucket_isr is
        an ISR entry address, returns (hist_main, hist_isr)."""
        hist = {}
        isr_hist = {}
        in_isr = 0
        sim = self.sim
        regs = sim.registers
        opcodes = sim.opcodes
        mem = sim.memory
        deck = self.deck
        fd, ia = self.frame_duration, self.int_active
        for _ in range(n):
            pc = regs[PC]
            if bucket_isr is not None:
                if pc == bucket_isr:
                    in_isr += 1
                tgt = isr_hist if in_isr else hist
            else:
                tgt = hist
            tgt[pc] = tgt.get(pc, 0) + 1
            if deck is not None and pc == TAPE_CALL_PC:
                self._tape()
                continue
            op = mem[pc]
            if interrupts and op == 0x76 and regs[IFF]:
                self._fast_halt()
                continue
            opcodes[op]()
            if bucket_isr is not None and in_isr and op == 0xED and mem[pc + 1] == 0x4D:
                in_isr -= 1
            if interrupts and regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
        return (hist, isr_hist) if bucket_isr is not None else hist

    # -- finding things (manual 8.2) ---------------------------------------
    def find_bytes(self, pattern, lo=0x4000, hi=0x10000):
        m = bytes(self.memobj.m)
        out = []
        p = lo
        pat = bytes(pattern)
        while True:
            p = m.find(pat, p, hi)
            if p < 0:
                break
            out.append(p)
            p += 1
        return out

    def save_state(self):
        """Full machine state: memory + registers + tape position.  Restoring
        it is exact, which is what makes the 40-key probe and every
        per-transition replay affordable (boot once, then poke: manual 6.4)."""
        return (bytes(self.memobj.m), list(self.sim.registers),
                self.deck.pos if self.deck else 0)

    def load_state(self, s):
        mem, regs, pos = s
        self.memobj.m[:] = mem
        self.sim.registers[:] = regs
        if self.deck:
            self.deck.pos = pos
        self.ports.release_all()

    def snapshot(self, lo=0x4000, hi=0x10000):
        return bytes(self.memobj.m[lo:hi])

    def diff(self, snap, lo=0x4000, hi=0x10000):
        m = self.memobj.m
        return [(lo + i, snap[i], m[lo + i]) for i in range(hi - lo)
                if m[lo + i] != snap[i]]

    # -- calling a routine in isolation (manual 6.6) ------------------------
    SENTINELS = (0xFE00, 0xFE02, 0xFE04, 0xFE06, 0xFE08, 0xFE0A)

    def call(self, addr, regs=None, limit=2_000_000, interrupts=False):
        """Call a routine with several DISTINCT sentinel return addresses
        pushed, so that however many returns the routine discards it lands on
        one of ours.  Breakpoints are PC comparisons, never byte patches.
        Returns (sentinel_index, tstates, steps): the index IS the routine's
        exit code -- 0 = returned normally, >0 = abandoned that many frames."""
        r = self.sim.registers
        sp = r[SP]
        for i, s in enumerate(reversed(self.SENTINELS)):
            sp = (sp - 2) & 0xFFFF
            self.memobj.m[sp] = s & 0xFF
            self.memobj.m[sp + 1] = s >> 8
        r[SP] = sp
        if regs:
            for k, v in regs.items():
                if k == 'HL':
                    r[H], r[L] = v >> 8, v & 0xFF
                elif k == 'DE':
                    r[D], r[E] = v >> 8, v & 0xFF
                elif k == 'BC':
                    r[B], r[C] = v >> 8, v & 0xFF
                elif k == 'IX':
                    r[IXh], r[IXl] = v >> 8, v & 0xFF
                elif k == 'IY':
                    r[IYh], r[IYl] = v >> 8, v & 0xFF
                else:
                    r[U.REGISTERS[k]] = v
        r[PC] = addr
        t0 = r[T]
        reason, steps = self.run_until(self.SENTINELS, limit=limit,
                                       interrupts=interrupts)
        if reason != 'target':
            return (None, r[T] - t0, steps)
        return (self.SENTINELS.index(r[PC]), r[T] - t0, steps)


def top(hist, n=20):
    return sorted(hist.items(), key=lambda kv: -kv[1])[:n]


if __name__ == '__main__':
    h = Harness()
    print(f'entry ${h.pc():04X}  SP ${h.regs[SP]:04X}  ROM: {"real 48K" if h.use_rom else "dummy"}')
    print('profiling 400000 instructions, interrupts OFF...')
    hist = h.profile(400_000, interrupts=False)
    print(f'PC after: ${h.pc():04X}   T={h.tstates()}  ({h.tstates()/CPU_HZ:.3f}s)')
    print(f'IM={h.regs[IM]} IFF={h.regs[IFF]} I=${h.regs[I]:02X}')
    print('hot addresses:')
    for a, c in top(hist, 15):
        print(f'  ${a:04X}  {c}')
    print(f'ports read: {sorted("0x%04X" % p for p in h.ports.reads)}')
    print(f'ROM writes ignored: {h.memobj.rom_writes}')
    print(f'tape stops: {h.tape_stops}  deck: {h.deck.log[:4]}')
