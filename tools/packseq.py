#!/usr/bin/env python3
"""
packseq.py -- drive the REAL Z80 through the DUNGEON SEQUENCE and print, per
level, which tape pack and which record inside it the game used.

    python tools/packseq.py 40            the level -> pack -> record table
    python tools/packseq.py --slice       the record index of all 31 packs
    python tools/packseq.py --check       the invariants below, as pass/fail

Same role as shotgate.py / doorgate.py: the original prints the expected
values, the port is diffed against them.

=============================================================================
THE GAME DOES NOT INDEX THE TAPE.  THE PACK INDEXES ITSELF.
=============================================================================
There is no pack number anywhere in the game.  $9203 asks the ROM for the next
block whose flag byte equals (IY+$4E) = $84CD and takes whatever the tape hands
over; the tape's own order is the level order.  What makes that work is that
EVERY PACK CARRIES ITS OWN CODE:

  $9278  LD HL,$C000 / PUSH HL       <- the 6 bytes it just zeroed
  $927E  LD B,6 / LD (HL),0 / INC HL / DJNZ
  $9283  RET                         <- RETURNS INTO THE PACK at $C000

so the loader's last act is to execute the thing it just loaded.  The pack's
prologue (identical in all 31 blocks, at pack offset +6) then:

  +$006  LD A,(IY+$4E) / CP $80      <- am I the FIRST pack?
  +$00D  LD (IY+$4D),A               <- $84CC = $80: "side 2 is at its start"
  +$010  LD (IY+$4E),$C0             <- ask for $C0 from now on
  +$014  LD HL,$C0D0 / JR +$07F      <- ship all 7 records as they stand
  +$019  (the $C0 path: pick four chunks at random, concatenate three)
  +$07F  LDIR $45A bytes -> $6F80, then $228 bytes -> $DDD8

$6F80..$73D9 (1114) and $DDD8..$DFFF (552) are the two free holes that survive
the relocation; they are the STASH, because $C000..$DAFF is the shadow screen
and is about to be overwritten.  $91D7 copies the stash back to $C000 before
walking to a record, which is why the walker at $91F0 always sees $C000.

So the $80/$C0 flag is not a pack number: $80 names the ONE block that holds
levels 1-7 and marks the start of side 2, and $C0 names "any later block".
=============================================================================
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, R, T, FRAME_T                      # noqa: E402
import tzx as tzxmod                                            # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
SIDE2 = os.path.join(ROOT, 'tape', 'Gauntlet - Side 2.tzx')

LEVEL = 0x8403          # the level number, BINARY, no clamp ($94CE / $B36B)
MASK = 0x84CC           # IY+$4D  bit7 "first pack loaded", bits0-2 records left
WANT = 0x84CD           # IY+$4E  the tape flag the next load asks for
TCD = 0x84BA            # IY+$3B  levels until the next treasure room
FLAGS = 0x847E          # IY-1    bit 6 = this level IS a treasure room
DONE = 0x847D           # IY-2    bit 7 levelDone, bit 3 exit consumed

PRO_LEN = 0xD0          # pack offset of the first record
TABLE = 0xB9            # pack offset of the 10 x 16-bit record-length table


def packs():
    _, blocks = tzxmod.parse(SIDE2)
    return [(b.data[0], b.data[1:-1]) for b in blocks
            if b.data is not None and len(b.data) >= 2]


def record_lengths(body):
    """The pack's own table.  Verified against the walker's rule below."""
    t = body[TABLE:TABLE + 20]
    return [x for x in (t[2 * k] | (t[2 * k + 1] << 8) for k in range(10)) if x]


def walk(body):
    """$91F3  LD C,(IX+0) / LD B,0 / BIT 7,(IX+2) / JR z / INC B / ADD IX,BC
    -- the record's length lives in its own first bytes."""
    off, out = PRO_LEN, []
    while off + 3 <= len(body) and body[off]:
        ln = body[off] + (256 if body[off + 2] & 0x80 else 0)
        out.append((off, ln))
        off += ln
    return out, off


def new_game(h):
    """$B35A..$B37E: level 1, no pack held, treasure countdown 2..5."""
    h.deck.rewind()
    h.deck.log = []
    h.poke(MASK, 0)
    h.poke(WANT, 0)
    h.poke(TCD, 4)


def one_level(h, lvl):
    """$B3D0: clear the level flags, then CALL $9175, which selects the record
    (loading a pack first if it has none left) and leaves it in IX."""
    h.poke(LEVEL, lvl)
    h.poke(0x84B8, 0)
    h.poke(FLAGS, 0)
    h.poke(DONE, 0)
    h.poke(0x84C0, 0)
    n0 = len(h.deck.log)
    t0 = h.regs[T]
    rc = h.call(0x9175, interrupts=True, limit=8_000_000)
    loaded = h.deck.log[n0:]
    m = h.memobj.m
    ix = h.ix()
    return {
        'ix': ix,
        'len': m[ix] + (256 if m[(ix + 2) & 0xFFFF] & 0x80 else 0),
        'load': loaded[0] if loaded else None,
        'mask': m[MASK], 'want': m[WANT], 'tcd': m[TCD],
        'treasure': bool(m[FLAGS] & 0x40),
        'frames': (h.regs[T] - t0) / FRAME_T,
        'rc': rc[0],
    }


def chunk_of(body, off):
    """Which numbered chunk does this pack offset start?  8 and 9 are the
    treasure rooms ($C09F: AND 1 / ADD A,8)."""
    cum = PRO_LEN
    for k, ln in enumerate(record_lengths(body)):
        if off == cum:
            return k
        cum += ln
    return None


def table(n):
    pk = packs()
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    new_game(h)
    blk = 0
    print('lvl  pack  rec  chunk  bytes  mask  cd  T  frames  note')
    for lvl in range(1, n + 1):
        r = one_level(h, lvl)
        note = ''
        if r['load']:
            if r['load'][0] != 'LOAD':
                print('%3d   --   tape exhausted (%s), the game waits for a $%02X '
                      'block for ever' % (lvl, r['load'][0], r['want']))
                break
            blk = r['load'][1]
            note = 'LOAD flag $%02X' % r['load'][2]
        body = pk[blk][1]
        # on a load pass IX points into the raw pack; otherwise into the stash
        ch = chunk_of(body, r['ix'] - 0xC000) if r['load'] else None
        rec = '' if r['load'] else str((r['ix'] - 0xC000))
        print('%3d  %4d  %4s  %5s  %5d  $%02X  %2d  %s  %6.1f  %s'
              % (lvl, blk, rec if rec else 'imm', '-' if ch is None else ch,
                 r['len'], r['mask'], r['tcd'], 'T' if r['treasure'] else '.',
                 r['frames'], note))


def slice_index():
    """The record index every build step needs: block, chunk, offset, length.
    Offsets and lengths only -- the bytes stay on this machine."""
    pk = packs()
    total = 0
    print('blk flag  bytes  recs  (chunk: offset, length) ...')
    for i, (flag, body) in enumerate(pk):
        lens = record_lengths(body)
        walked, end = walk(body)
        assert [w[1] for w in walked] == lens, 'block %d: walker disagrees' % i
        assert len(body) - end == 1 and body[-1] == 0xFF, \
            'block %d: no $FF terminator' % i
        total += len(lens)
        spans = ' '.join('%d:+$%04X,%d' % (k, w[0], w[1])
                         for k, w in enumerate(walked))
        print('%3d $%02X %6d %5d  %s' % (i, flag, len(body), len(lens), spans))
    print('\n%d records on side 2 (7 fixed + 30 packs x 10)' % total)


def check():
    pk = packs()
    fails = []

    def ok(name, cond, detail=''):
        print('%-58s %s %s' % (name, 'PASS' if cond else 'FAIL', detail))
        if not cond:
            fails.append(name)

    ok('31 blocks: one $80 then thirty $C0',
       [p[0] for p in pk] == [0x80] + [0xC0] * 30)
    walks = [walk(p[1]) for p in pk]
    ok('the walker rule reproduces every pack\'s own length table',
       all([w[1] for w in wk] == record_lengths(p[1])
           for (wk, _), p in zip(walks, pk)))
    ok('every pack ends in exactly one spare byte, $FF',
       all(len(p[1]) - end == 1 and p[1][-1] == 0xFF
           for p, (_, end) in zip(pk, walks)))
    ok('record counts are 7 + 30x10 = 307',
       sum(len(w[0]) for w in walks) == 307,
       '(%d)' % sum(len(w[0]) for w in walks))
    ok('pack length word at +0 equals the block length',
       all(p[1][0] | (p[1][1] << 8) == len(p[1]) for p in pk))
    pro = [bytes(p[1][6:TABLE]) for p in pk]
    diffs = {k for a in pro for k in range(len(pro[0])) if a[k] != pro[0][k]}
    ok('one prologue, shared by all 31 packs (bar the $C071 self-patch)',
       diffs == {0x071 - 6}, 'differing offsets %s' % sorted(diffs))
    worst = 0
    for _, body in pk:
        L = record_lengths(body)
        if len(L) < 10:
            continue
        worst = max(worst, sum(sorted(L[:8], reverse=True)[:3]),
                    sum(sorted(L[:8], reverse=True)[:2]) + max(L[8:]))
    ok('the +$068 size guard ($678) can never fire', worst <= 0x678,
       'worst three-chunk sum %d vs %d' % (worst, 0x678))

    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    new_game(h)
    seq, treas = [], 0
    for lvl in range(1, 128):
        r = one_level(h, lvl)
        if r['load']:
            seq.append((lvl, r['load'][1]))
        treas += r['treasure']
    ok('levels 1-7 come from block 0, one record each',
       seq[0] == (1, 0) and len(seq) == 31)
    ok('blocks are consumed in strict tape order, one per four levels',
       [b for _, b in seq] == list(range(31))
       and [l for l, _ in seq] == [1] + list(range(8, 128, 4)))
    print('\n%d treasure rooms in 127 levels' % treas)
    return 1 if fails else 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--slice' in args:
        slice_index()
    elif '--check' in args:
        sys.exit(check())
    else:
        table(int(args[0]) if args else 24)
