#!/usr/bin/env python3
"""
teleportgate.py -- THE $30 TELEPORT-PAD DIFFERENTIAL.

The pad ($A6B0 / $B195) is the one interaction dungeon 1 cannot exercise, so
nothing in the existing gate set touches it.  This tool plants $30 cells in
dungeon 1's live grid on BOTH sides and drives the same key script through
each, so the pad chain gets a differential of its own.

    python tools/teleportgate.py            the full matrix, both sides
    python tools/teleportgate.py z80        the real Z80 only
    python tools/teleportgate.py port       the built engine only

The matrix is APPROACH-THEN-ESCAPE, because the whole point is the state the
pad leaves behind.  Holding INTO a pad is indistinguishable from a wall on
both sides; the disagreement only shows when the key is RELEASED and another
direction pressed, which is exactly what the play report described and exactly
what the earlier "the original sticks too" measurement never did.

THE RULE THIS GATE CARRIES (measured, see NOTES-engine.md):

  $A6B0  SET 1,(IX+14)          the pad's whole dispatcher arm
  $A4FF  BIT 1,(IX+14) / JP nz,$B195     the NEXT pass's move is REPLACED
  $B195  the state machine.  EVERY exit from it clears bit 1 within at most
         four passes -- $B218 (no destination), $B216->$B218 (no candidate),
         $B1F8/$B1FD (the leash), $B221/$B225 (the flight ended).  There is
         no path on which the arm survives, so "armed" is a ONE-PASS state
         unless a teleport is actually in flight.

Scenario 1 has ONE pad, so $B246 finds no candidate (the source scores 0 and
is skipped) and $B195 bails -- the arm/clear cycle with nothing else.  The
player must still walk away in any direction that does not re-probe the pad.
Scenario 2 has TWO, so the teleport fires.
"""
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')

# cell (col,row) -> $8000-relative address
def cell(c, r):
    return 0x8000 + (r * 32) + c


# (label, [(col,row,value)], approach dir, approach passes, escape dir, escape passes)
MATRIX = []
for _scn, _plants in (
        ('1pad', [(3, 4, 0x30)]),
        ('2pad', [(3, 4, 0x30), (6, 4, 0x30)])):
    for _esc in ('up', 'left', 'right', 'down', None):
        MATRIX.append((f'{_scn}/escape-{_esc}', _plants, 'down', 6, _esc, 10))


def run_z80():
    from harness import Harness                                  # noqa: E402
    from keyprobe import keymask                                 # noqa: E402
    from sim_move import step_to_loop_top, DIRKEY, KM            # noqa: E402
    out = []
    for label, plants, ad, an, ed, en in MATRIX:
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        m = h.memobj.m
        for c, r, v in plants:
            m[cell(c, r)] = v
        step_to_loop_top(h)
        rows = []
        for d, n in ((ad, an), (ed, en)):
            h.ports.release_all()
            if d:
                sel, bit = KM[DIRKEY[d]]
                h.ports.press(sel, keymask(bit))
            for _ in range(n):
                step_to_loop_top(h)
                rows.append((m[0x8420], m[0x8421]))
        out.append((label, rows[an - 1], rows[-1]))
    return out


PORT_JS = r'''
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm');
const BUILT=path.join(__dirname,'..','web','gauntlet.html');
const ctxStub={set fillStyle(v){this._f=v;},get fillStyle(){return this._f;},fillRect(){}};
function makeEl(id){const s=new Set();return{id,_text:'',innerHTML:'',
 get textContent(){return this._text;},set textContent(v){this._text=String(v);},
 getContext(){return ctxStub;},
 classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
 width:256,height:192};}
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
const MATRIX=JSON.parse(process.argv[2]);
const out=[];
for(const t of MATRIX){
  const g=G.seed({});
  for(const p of t.plants) g.map[p[1]][p[0]]=p[2];
  const rows=[];
  for(const s of [[t.ad,t.an],[t.ed,t.en]]){
    for(let i=0;i<s[1];i++){ g.onePass(s[0]?{[s[0]]:true}:{}); rows.push([g.x,g.y]); }
  }
  out.push([t.label, rows[t.an-1], rows[rows.length-1]]);
}
console.log(JSON.stringify(out));
'''


def run_port():
    import json
    js = os.path.join(ROOT, 'build', '_teleportgate_port.js')
    open(js, 'w').write(PORT_JS)
    spec = [dict(label=l, plants=[list(p) for p in pl], ad=ad, an=an, ed=ed, en=en)
            for l, pl, ad, an, ed, en in MATRIX]
    r = subprocess.run(['node', js, json.dumps(spec)],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(1)
    return [(a, tuple(b), tuple(c)) for a, b, c in json.loads(r.stdout)]


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    z = run_z80() if what in ('all', 'z80') else None
    p = run_port() if what in ('all', 'port') else None
    if z and p:
        bad = 0
        print('%-22s %-22s %-22s' % ('scenario', 'Z80 after approach/escape',
                                     'PORT after approach/escape'))
        for (l, za, ze), (_, pa, pe) in zip(z, p):
            ok = (za == pa and ze == pe)
            bad += 0 if ok else 1
            print('%-22s %-22s %-22s %s' % (
                l, '%s -> %s' % (za, ze), '%s -> %s' % (pa, pe),
                'ok' if ok else 'MISMATCH'))
        print('\n%d mismatching rows of %d' % (bad, len(z)))
        raise SystemExit(1 if bad else 0)
    for l, a, e in (z or p):
        print('%-22s approach %s  escape %s' % (l, a, e))


if __name__ == '__main__':
    main()
