/* render_shot.js -- rasterize the SHIPPED engine's output headlessly.
 *
 * This is the payoff for the phase-12 constraint that the renderer may use only
 * primitives a 30-line stub can rasterize (manual 7.5).  The engine paints
 * exclusively through fillRect with a solid fillStyle, so the whole 256x192
 * frame falls out of the shim below -- no canvas, no browser.
 *
 *   node tools/render_shot.js out.ppm [dir] [passes]
 *
 * Writes a binary PPM (P6); convert with tools/ppm2png.py.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.dirname(__dirname);
const BUILT = path.join(ROOT, 'web', 'gauntlet.html');

const W = 256, H = 192;
const buf = new Uint8Array(W * H * 3);

function hex(c) {
  if (c[0] === '#') {
    return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16),
            parseInt(c.slice(5, 7), 16)];
  }
  return [255, 0, 255];
}

const ctxStub = {
  _fill: '#000000',
  set fillStyle(v) { this._fill = v; },
  get fillStyle() { return this._fill; },
  fillRect(x, y, w, h) {
    const [r, g, b] = hex(this._fill);
    for (let yy = Math.max(0, y | 0); yy < Math.min(H, (y | 0) + (h | 0)); yy++)
      for (let xx = Math.max(0, x | 0); xx < Math.min(W, (x | 0) + (w | 0)); xx++) {
        const o = (yy * W + xx) * 3;
        buf[o] = r; buf[o + 1] = g; buf[o + 2] = b;
      }
  },
};

function makeEl() {
  return { _text: '', innerHTML: '',
           get textContent() { return this._text; },
           set textContent(v) { this._text = String(v); },
           getContext: () => ctxStub, width: W, height: H,
           classList: { add() {}, remove() {}, contains: () => false } };
}
const els = new Map();
const sandbox = {
  console,
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  document: { getElementById(id) {
    if (!els.has(id)) els.set(id, makeEl());
    return els.get(id);
  } },
  addEventListener() {},
  requestAnimationFrame() { return 1; },
  Math, JSON, Uint8Array, Buffer, String, Number, Array, Object, Error,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const html = fs.readFileSync(BUILT, 'utf8');
const j = html.match(/<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
els.set('assets', Object.assign(makeEl(), { _text: j[1].replace(/<\\\//g, '</') }));
const code = html.match(/<script>([\s\S]*?)<\/script>\s*$/)[1];
vm.runInContext(code, sandbox, { filename: 'gauntlet.html' });

const G = sandbox.globalThis.__GAUNTLET__;
const out = process.argv[2] || 'shot.ppm';
const dir = process.argv[3] || null;
const passes = Number(process.argv[4] || 0);

/* "idle"/"none" means run the passes with no key held.  Note there is no
   longer any poking of g.dir after the loop: the sprite's compass slot and
   walk phase are engine state maintained by onePass ($A47B keeps the
   persisted slot when nothing is held), so forcing g.dir here would render a
   pose the simulation never reached. */
const held = (dir && dir !== 'idle' && dir !== 'none') ? dir : null;
/* --fire holds Z from pass `--walk W` on (default 0).  Holding fire FREEZES
   the player ($A57E), so `--walk` is how you get him somewhere first. */
const argv = process.argv;
const optN = (k, d) => argv.indexOf(k) >= 0 ? Number(argv[argv.indexOf(k) + 1]) : d;
const wantFire = argv.includes('--fire');
const walk = optN('--walk', 0);
/* --menu PHASE  --  rasterize the FRONT END (block A) at a named phase, or
   --attract N for the game's own ranked-table page N, or --rewind for the
   cold-boot REWIND TAPE prompt.  The menu is driven with the SAME key script
   shape the Z80 harness uses (SPACE tapped 6 frames on / 10 off, because
   $C86C waits for a RELEASE), plus any extra keys named by --key.        */
if (argv.includes('--menu')) {
  const want = argv[argv.indexOf('--menu') + 1] || 'stoptape';
  const extra = argv.indexOf('--key') >= 0 ? argv[argv.indexOf('--key') + 1] : '';
  const settle = optN('--settle', 0);
  const F = G.frontend;
  const fe = new F.FrontEnd(), kb = new F.Keyboard(), ev = [];
  let n = 0;
  while (n < 40000 && fe.phase !== want) {
    if (n % 16 < 6) kb.press('SPACE'); else kb.releaseAll();
    if (fe.frame(kb, ev, n)) break;
    n++;
  }
  kb.releaseAll();
  for (const k of extra.split(',').filter(Boolean)) {
    for (let j = 0; j < 15; j++) { kb.press(k); fe.frame(kb, ev, n++); }
    for (let j = 0; j < 15; j++) { kb.releaseAll(); fe.frame(kb, ev, n++); }
  }
  for (let j = 0; j < settle; j++) fe.frame(kb, ev, n++);
  G.frontend.renderScreen(ctxStub, fe.scr, fe.frameCtr);
  fs.writeFileSync(out, Buffer.concat([
    Buffer.from(`P6\n${W} ${H}\n255\n`), Buffer.from(buf)]));
  console.log(`wrote ${out}  phase=${fe.phase} frame=${n} ` +
              `bytes=${fe.bytes().map(v => v.toString(16)).join(',')}`);
  process.exit(0);
}
if (argv.includes('--attract') || argv.includes('--rewind')) {
  const g0 = G.seed({});
  g0.packHeld = true;
  g0.enterAttract();
  const page = optN('--attract', 1);
  for (let i = 1; i < page; i++) g0.attractDraw();
  if (argv.includes('--rewind')) { g0.packHeld = false; g0.attractTick({fire:true}); }
  G.render(ctxStub, g0);
  fs.writeFileSync(out, Buffer.concat([
    Buffer.from(`P6\n${W} ${H}\n255\n`), Buffer.from(buf)]));
  console.log(`wrote ${out}  mode=${g0.mode} page=${g0.attractPage}`);
  process.exit(0);
}
const g = G.seed({});
/* --level N starts the run in dungeon N ($B3D0 -> $9175 -> $97CB), so a
   later dungeon can be LOOKED AT rather than reasoned about.  --c7 forces
   $84C7, the byte $91B5 draws, which is the mirror the original would pick
   at random on any level >= 8. */
if (argv.includes('--level')) {
  const lv = optN('--level', 1);
  /* THE GAME NEVER INDEXES THE TAPE: a $C0 pack serves four levels and the
     next one is only loaded when the mask empties, so "dungeon 22" means
     "the 22nd level of this run", not the 22nd thing on the tape.  Walk the
     progression rather than jumping, or every level >= 8 comes out as the
     first draw of the first $C0 pack. */
  for (let l = 1; l <= lv; l++) g.startLevel(l);
  if (argv.includes('--c7')) {
    /* $91B5's byte is a draw, so ask for the level again until the two bits
       the mirrors read come out as wanted -- no engine hook required. */
    const want = optN('--c7', 0) & 3;
    for (let i = 0; i < 64 && (g.mirrorBits & 3) !== want; i++) g.startLevel(lv);
  }
}
/* --dead kills the player outright: health BCD 0000 is what $93D2 tests, and
   the two passes after it are the death and the main loop's own return.
   --type NAME then types a name into the $B3B9 entry, one letter every 8
   video frames, which is the gate at $930D. */
if (argv.includes('--dead')) {
  g.health = 0;
  g.advance(1.0, {});
  const nm = argv.indexOf('--type') >= 0 ? argv[argv.indexOf('--type') + 1] : '';
  for (const ch of nm.toUpperCase()) {
    const target = ch === ' ' ? 0x20 : ch.charCodeAt(0);
    for (let i = 0; i < 40 && g.name[g.nameCol] !== target; i++)
      for (let f = 0; f < 8; f++) g.advance(1 / 50.08, { up: true });
    for (let f = 0; f < 8; f++) g.advance(1 / 50.08, { right: true });
  }
}
/* --p2 [DIR] -- put PLAYER 2 in the game the way a second player really
   joins: one pass with his FIRE held ($9440's only gate), then the six
   materialise passes, and after that DIR is held for him.  There is no
   player-count switch to set; he is a player who has pressed fire. */
const wantP2 = argv.includes('--p2');
const p2dir = (() => { const i = argv.indexOf('--p2');
                       const v = i >= 0 ? argv[i + 1] : null;
                       return (v && !v.startsWith('--')) ? v : null; })();
if (wantP2) {
  g.onePass({ p2: { fire: true } });
  for (let i = 0; i < 7; i++) g.onePass({ p2: {} });   // the materialise
}
for (let i = 0; i < passes; i++) {
  const inp = held ? { [held]: true } : {};
  if (wantFire && i >= walk) inp.fire = true;
  if (wantP2) inp.p2 = p2dir ? { [p2dir]: true } : {};
  g.onePass(inp);
}
G.render(ctxStub, g);

fs.writeFileSync(out, Buffer.concat([
  Buffer.from(`P6\n${W} ${H}\n255\n`), Buffer.from(buf)]));
console.log(`wrote ${out}  player at (${g.x},${g.y}) cam (${g.camX},${g.camY}) ` +
            `dir $${g.dir.toString(16)}`);
