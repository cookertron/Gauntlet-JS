#!/usr/bin/env python3
"""
warpgate.py -- LEVEL 1's WARP TILES DO NOT LAND ON THE "CANONICAL" DUNGEON.

Reported from play: taking the portal on level 1 that jumps to level 8 loads
a different dungeon than build/dungeons/dungeon-08.png (the reference render,
made by tools/dungeonshot.js replaying levels 1..8 IN ORDER).  Investigated
here and found to be the ORIGINAL's own behaviour, not a port bug:

    python tools/warpgate.py          the real-Z80 measurement, and the port
                                       check against it

selectAndBuild()'s own docstring already says it is not a function of the
level number: below level 8 the dungeon comes from the tape's $80 pack (a
per-level lookup, no state); from level 8 on it comes from a rotating stash
whose PACK MASK ($84CC) and TREASURE COUNTDOWN ($84BA) persist across every
call, warp or not.  A warp ($37/$38, doExit) skips straight to the target
level -- it does not re-run the levels in between -- so by the time it
reaches level 8 far fewer of those calls have happened than a player who
walked levels 2..7 first.

MEASURED on the real Z80 (packseq.one_level, same machinery packseq.py's
own --check uses): warping 1->8 and playing 1..8 both reach level 8's build
with mask=$80, tcd=4 -- IDENTICAL bookkeeping, because levels 2..7 never
touch either byte (they are all served from the one $80 pack already held).
So the mask/tcd state is NOT what makes the two dungeons different.  What
differs is how many Z80 instructions ran first: the warp path built one
level before this one, the played-through path built seven, and the pack's
own stash draw is `LD A,R` -- the same irreducible, timing-sensitive entropy
source as the treasure room's ($A31A, see selectAndBuild's comment) and the
potion's ($AF7C, see potiongate.py).  Different instruction counts, different
R, different record: block 1 chunk 4 (189 bytes) warping in, block 1 chunk 3
(318 bytes) playing through, on this build.

So: build/dungeons/dungeon-08.png is ONE canonical level 8 (the one you get
by never warping), not THE level 8.  A warp is supposed to hand you a
different one, on the original as much as on the port -- this is declared,
like every other LD A,R site, not fixed, because there is nothing to fix.

This tool locks in the part that COULD regress: that the port's mask/tcd
bookkeeping tracks the original's byte for byte along both histories, so
that if the dungeons ever do differ it is for the declared reason (R timing)
and not a state-tracking bug the port introduced on its own.
"""
import json
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                      # noqa: E402
import packseq as ps                                              # noqa: E402

WARP = [1, 8]
CANON = [1, 2, 3, 4, 5, 6, 7, 8]


def real_run(levels):
    h = Harness()
    h.load_state(pickle.load(open(ps.STATE, 'rb')))
    ps.new_game(h)
    pk = ps.packs()
    blk = 0
    out = []
    for lvl in levels:
        r = ps.one_level(h, lvl)
        if r['load']:
            blk = r['load'][1]
        body = pk[blk][1]
        ch = ps.chunk_of(body, r['ix'] - 0xC000) if r['load'] else None
        rec = None if r['load'] else (r['ix'] - 0xC000)
        out.append({'lvl': lvl, 'blk': blk, 'chunk': ch, 'rec_off': rec,
                    'len': r['len'], 'mask': r['mask'], 'tcd': r['tcd']})
    return out


# ===========================================================================
# THE PORT, asked for the same two histories -- $84CC and $84BA are
# `tape.mask` and `tcd`, sampled the instant BEFORE level 8's build (so
# "entering" matches the real side's post-level-7/post-level-1 numbers) and
# the instant after (so "leaving" does too), plus a content fingerprint that
# does not care WHICH record it is, only whether the two histories agree.
# ===========================================================================
PORT_JS = r'''
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm');
const BUILT=path.join(__dirname,'..','web','gauntlet.html');
const ctxStub={set fillStyle(v){this._f=v;},get fillStyle(){return this._f;},fillRect(){}};
function makeEl(id){const s=new Set();return{id,_text:'',innerHTML:'',
 get textContent(){return this._text;},set textContent(v){this._text=String(v);},
 getContext(){return ctxStub;},
 classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
 blur(){},focus(){},width:256,height:192};}
const els=new Map();
const sandbox={console,atob:s=>Buffer.from(s,'base64').toString('binary'),
 document:{getElementById(id){if(!els.has(id))els.set(id,makeEl(id));return els.get(id);}},
 addEventListener(){},requestAnimationFrame(){return 1;},
 Math,JSON,Uint8Array,Buffer,String,Number,Array,Object,Error};
sandbox.globalThis=sandbox;vm.createContext(sandbox);
const html=fs.readFileSync(BUILT,'utf8');
const jm=html.match(/<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
els.set('assets',Object.assign(makeEl('assets'),
  {_text:jm[1].split(String.fromCharCode(60,92,47)).join('</')}));
const cm=html.match(/<script>([\s\S]*?)<\/script>\s*$/);
vm.runInContext(cm[1],sandbox,{filename:'gauntlet.html'});
const G=sandbox.globalThis.__GAUNTLET__;

function fingerprint(g){
  let h=0;
  for(let r=0;r<32;r++) for(let c=0;c<32;c++) h=((h*33)+g.map[r][c])>>>0;
  return h;
}

// `new G.Game({})`, NOT `G.seed({})` -- G.seed() resets and returns the
// PAGE'S OWN SINGLETON `game`, so two `G.seed({})` calls in one process are
// the SAME object twice, and the second call's build retroactively changes
// what the first variable reads.  Found by this tool: an early comparison
// script read a stable fingerprint right after building the "warp" case,
// then a different one once the "canon" case had also been built, though
// nothing had touched the warp variable in between.  `Game` the class is
// exported for exactly this -- a real second instance.
//
// WARP: reset() already built level 1 once (its own selectAndBuild(1)
// call) -- jump straight to 8, exactly what doExit's $38 tile does, with
// nothing in between to touch the build stream (actor coins/generator
// rolls live on the PLAY stream, not this one).
const gw = new G.Game({});
const before_w = {mask: gw.tape.mask, tcd: gw.tcd};
gw.startLevel(8);
const after_w = {mask: gw.tape.mask, tcd: gw.tcd};

// CANON: the same replay dungeonshot.js/jumpToLevel use -- 1..7 first.
const gc = new G.Game({});
for (let l = 2; l <= 7; l++) gc.startLevel(l);
const before_c = {mask: gc.tape.mask, tcd: gc.tcd};
gc.startLevel(8);
const after_c = {mask: gc.tape.mask, tcd: gc.tcd};

// the real exit tile fires the same transition doExit -> levelEnd ->
// levelOver would: confirm the $38 tile actually asks for level 8.
const gReal = new G.Game({});
gReal.mode = 'play';
let warpTile = null;
for (let r = 0; r < 32 && !warpTile; r++)
  for (let c = 0; c < 32; c++)
    if ((gReal.map[r][c] & 0x7F) === 0x38) { warpTile = [c, r]; break; }
let exitLevelOwn = null;
if (warpTile){
  gReal.doExit(0x38, {x: warpTile[0]*4, y: warpTile[1]*4});
  exitLevelOwn = gReal.players[0].levelOwn;
}

console.log(JSON.stringify({
  before_w, after_w, before_c, after_c,
  fp_w: fingerprint(gw), fp_c: fingerprint(gc),
  warpTile, exitLevelOwn,
}));
'''


def port_run():
    js = os.path.join(ROOT, 'build', '_warpgate_port.js')
    open(js, 'w').write(PORT_JS)
    r = subprocess.run(['node', js], capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(1)
    return json.loads(r.stdout)


def main():
    real_w = real_run(WARP)
    real_c = real_run(CANON)
    rw8, rc8 = real_w[-1], real_c[-1]

    print('REAL Z80')
    print('  warp  1->8  : entering mask=$%02X tcd=%d   picked blk=%d chunk=%s rec=%s len=%d'
          % (real_w[0]['mask'], real_w[0]['tcd'], rw8['blk'], rw8['chunk'],
             rw8['rec_off'], rw8['len']))
    print('  canon 1..8  : entering mask=$%02X tcd=%d   picked blk=%d chunk=%s rec=%s len=%d'
          % (real_c[-2]['mask'], real_c[-2]['tcd'], rc8['blk'], rc8['chunk'],
             rc8['rec_off'], rc8['len']))

    assert (real_w[0]['mask'], real_w[0]['tcd']) == \
           (real_c[-2]['mask'], real_c[-2]['tcd']), \
        'the real machine enters level 8 with DIFFERENT bookkeeping between ' \
        'the two histories -- the theory below is wrong, go remeasure'
    assert (rw8['blk'], rw8['chunk'], rw8['rec_off'], rw8['len']) != \
           (rc8['blk'], rc8['chunk'], rc8['rec_off'], rc8['len']), \
        'the real machine picked the SAME record both ways -- the reported ' \
        'mismatch cannot be this mechanism after all'
    print('  -> identical bookkeeping, different record: CONFIRMED on real hardware')

    port = port_run()
    print()
    print('PORT')
    print('  warp  before=%s after=%s' % (port['before_w'], port['after_w']))
    print('  canon before=%s after=%s' % (port['before_c'], port['after_c']))
    print('  warp tile found at', port['warpTile'],
          '-> doExit sets levelOwn =', port['exitLevelOwn'])

    assert port['warpTile'] is not None, \
        'no $38 warp tile in this build of dungeon 1 -- can not reach level 8 from it'
    assert port['exitLevelOwn'] == 8, \
        'the $38 tile does not ask for level 8: levelOwn = %r' % port['exitLevelOwn']
    assert port['before_w'] == port['before_c'], \
        'the port\'s mask/tcd bookkeeping has DIVERGED between the two ' \
        'histories -- they should enter level 8 identically, as measured above'
    assert port['before_w']['mask'] == real_w[0]['mask'] == 0x80, \
        'the port\'s pack mask does not match the real Z80\'s ($84CC): ' \
        'mask is a deterministic per-level lookup below level 8, not an ' \
        'LD A,R draw, so this one SHOULD match exactly'
    # tcd is NOT compared to the real machine's raw number: packseq's
    # new_game() pokes tcd=4 for a reproducible test, sidestepping $B377's
    # own `2 + (LD A,R & 3)` -- there is no fixed "real" tcd to match, only
    # the port's OWN two histories agreeing with each other (asserted above).
    assert port['fp_w'] != port['fp_c'], \
        'the port built the SAME level 8 both ways -- it should not, matching ' \
        'the real machine'
    print('  -> matches the real machine: same bookkeeping, different dungeon')
    print()
    print('NOT A BUG: build/dungeons/dungeon-08.png is level 8 reached by')
    print('playing straight through 1..7 first.  A warp reaches level 8 with')
    print('much less of the build stream spent, so LD A,R samples differently')
    print('at the stash draw and a different record comes out -- on the')
    print('original as much as on the port.')


if __name__ == '__main__':
    main()
