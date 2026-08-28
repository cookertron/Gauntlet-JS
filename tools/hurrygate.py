import os
#!/usr/bin/env python3
"""
hurrygate.py -- THE HURRY-UP, $8531 CALL $971B.

The level's TIME LIMIT, and it is a limit on IDLENESS rather than on elapsed
time: $84B8 counts drain ticks (one per 64 video frames, $B6E9) but every
shot ($8C90), every kill ($AEE5), every pickup ($A7D0) and $9081 reset it to
zero.  Playing delays it; standing still does not.

    python tools/hurrygate.py          measure on the Z80, write build/_hurry2.json
    node tools/headless.js             compares the built engine against it

TWO STAGES, each latched in its own bit of $847D and each fired once a level:

  STAGE 1  $975B, at $84B8 >= $17 (23 ticks)   -- sound 14
    over all 1024 cells, on the RAW byte (no AND $7F, so bit 7 set is skipped)
      $11, $12 -> 0      the DOORS open
      $1F      -> 0      and the KEYS on the floor vanish with them
      $32      -> 0

  STAGE 2  $972E, at $84B8 >= $8C (140 ticks) AFTER stage 1 reset it -- sound 16
    over all 1024 cells, on (byte AND $7F)
      1..$10   -> $36    EVERY WALL BECOMES AN EXIT
      $33..$35 -> $36    and so does a destructible wall

  $971B BIT 6,(IY-1) / RET nz -- a TREASURE ROOM never hurries.

MEASURED, unpoked: from the captured state stage 1 fires on pass 305 with
$84B8 running 4 -> $17, i.e. 19 ticks x 64 frames = 1,216 frames at ~4 frames
a pass.  Stage 2 would need 140 more ticks -- about 2,240 passes -- so this
tool DRIVES $84B8 to each threshold rather than simulating them, which tests
the threshold and the transform but NOT the tick rate.  The tick rate is
$B6E9 and is already gated by the drain in tools/p2gate.py.

The planted cells cover every arm of both ladders including the ones that must
NOT change: $36 (already an exit), $20 (a generator) and $8F (bit 7 set).
"""

import sys, pickle, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, PC, IFF, TAPE_CALL_PC
from sim_move import LOOP_TOP
S=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'build','state_48k.pkl')
def passn(h, sfx):
    sim=h.sim; regs=sim.registers; op=sim.opcodes; mem=sim.memory
    fd,ia=h.frame_duration,h.int_active; n=0
    while n<20_000_000:
        pc=regs[PC]
        if n and pc==LOOP_TOP: return
        if h.deck is not None and pc==TAPE_CALL_PC: h._tape(); n+=1; continue
        if mem[pc]==0x76 and regs[IFF]: h._fast_halt(); n+=1; continue
        if pc==0xBA2B: sfx.append(regs[0])
        op[mem[pc]](); n+=1
        if regs[IFF] and regs[25]%fd<ia: sim.accept_interrupt(regs,mem,pc)
h=Harness(); h.load_state(pickle.load(open(S,'rb'))); m=h.memobj.m
passn(h,[])
# Every arm of both ladders, including the ones that must NOT change.
# $91/$9F/$B2 are the SAME values with bit 7 set: stage 1 reads the RAW byte,
# so they must survive stage 1 -- masking them would clear them, and without
# these three that difference is invisible (a mutation adding AND $7F to
# stage 1 was caught by nothing until they were planted).
PLANT=[(6,2,0x11),(7,2,0x12),(8,2,0x1F),(9,2,0x32),(10,2,0x33),(11,2,0x35),
       (12,2,0x36),(13,2,0x20),(14,2,0x8F),
       (16,2,0x91),(17,2,0x9F),(18,2,0xB2)]
for c,r,v in PLANT: m[0x8000+r*32+c]=v
before=list(m[0x8000:0x8400])
out={'plant':PLANT,'before':before}
for stage,thr in (('doors',0x17),('exits',0x8C)):
    for _ in range(400):
        m[0x84B8]=thr                       # drive the counter to the edge
        sfx=[]; passn(h,sfx)
        if 14 in sfx or 16 in sfx:
            out[stage]={'sfx':[s for s in sfx if s in (14,16)],
                        'map':list(m[0x8000:0x8400]),
                        'f847D':m[0x847D]&3,'hurry':m[0x84B8]}
            print('%-6s fired: sfx=%s  $847D&3=%d  $84B8=%02X'
                  %(stage,out[stage]['sfx'],out[stage]['f847D'],out[stage]['hurry']))
            break
    else:
        print(stage,'NEVER FIRED'); out[stage]=None
json.dump(out, open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'build','_hurry2.json'),'w'))
