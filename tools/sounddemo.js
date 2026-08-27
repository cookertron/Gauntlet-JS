/* sounddemo.js -- THE FILES THE OWNER LISTENS TO (manual phase 16).
 *
 *     node tools/sounddemo.js
 *
 * Written for a PLAY REPORT: "the sound now has slight noise to it".  Two
 * questions had to be separated, so there are two pairs of files, and every
 * pair shares ONE gain -- build/beeper_*.wav are peak-normalised
 * individually, which is right for measuring a pitch back out and wrong for
 * an A/B, because it hides exactly the difference you are listening for.
 *
 *   1  IS THE ROUGHNESS THE GAME'S?
 *      demo_1 / demo_2   the SAME effect (id 7, a key) in a quiet dungeon
 *                        and at a generator cluster
 *      demo_3 / demo_4   the SAME walk under the shipped pass clock and
 *                        under the flat four-frame clock that preceded it
 *      The original does this too, and by more: `python tools/loadsound.py`
 *      and the table in notes/NOTES-engine.md.  A pass is one chirp
 *      ($B8FB, one call site at $9CD9), so the pass cost IS the cadence.
 *
 *   2  WAS THERE AN ARTEFACT?
 *      demo_5 / demo_6   the front end's title tune through the bridge as it
 *                        shipped and as it is now
 *      demo_7            the difference alone -- the noise the bridge added,
 *                        at the same gain, so its level is honest
 *
 * The "before" arm is not a different build: it is the shipped artifact with
 * SoundOut.flushBeeper's pre-fix arithmetic monkey-patched back on, quoted
 * verbatim below, so this stays reproducible after the old file is gone.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.dirname(__dirname);
const BUILT = path.join(ROOT, 'web', 'gauntlet.html');
const SR = 44100;
const DT = 1 / 60;

/* ---------- the recorder, the same shape tools/soundwav.js uses -------- */
function load() {
  const rec = [];
  const st = { t: 0 };
  class StubAudioContext {
    constructor() { this.sampleRate = SR; this.destination = {}; }
    get currentTime() { return st.t; }
    createGain() { return { gain: { value: 1 }, connect() {} }; }
    createBuffer(nch, len) {
      const d = new Float32Array(len);
      return { numberOfChannels: nch, length: len, sampleRate: SR,
               getChannelData() { return d; } };
    }
    createBufferSource() {
      return { buffer: null, connect() {},
               start(when) { rec.push({ when,
                                        data: this.buffer.getChannelData() }); } };
    }
  }
  const ctxStub = { set fillStyle(v) { this._f = v; },
                    get fillStyle() { return this._f; }, fillRect() {} };
  const makeEl = id => ({ id, _text: '', innerHTML: '',
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    getContext: () => ctxStub,
    classList: { add() {}, remove() {}, contains: () => false },
    width: 256, height: 192 });
  const els = new Map();
  const sandbox = { console,
    atob: s => Buffer.from(s, 'base64').toString('binary'),
    document: { getElementById(id) { if (!els.has(id)) els.set(id, makeEl(id));
                                     return els.get(id); } },
    addEventListener() {}, requestAnimationFrame() { return 1; },
    AudioContext: StubAudioContext,
    Math, JSON, Uint8Array, Float32Array, Buffer, String, Number, Array,
    Object, Error, Map, Set };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const html = fs.readFileSync(BUILT, 'utf8');
  const j = html.match(
    /<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
  els.set('assets', Object.assign(makeEl('assets'),
                                  { _text: j[1].replace(/<\\\//g, '</') }));
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>\s*$/)[1], sandbox,
                  { filename: 'gauntlet.html' });
  const G = sandbox.globalThis.__GAUNTLET__;
  if (G.sound.mode() !== G.sound.SOUND_48K) G.sound.setMode(G.sound.SOUND_48K);
  const out = G.sound.out;
  out.start();
  out.chip = new G.sound.BeeperChip(SR);
  return { G, game: G.game, out, rec, adv: dt => { st.t += dt; } };
}

/* ---------- THE BRIDGE AS IT SHIPPED BEFORE THE FIX --------------------
   Verbatim, so the A/B is against the real thing and not a paraphrase.  The
   defect is `Math.round(nf*spf)` for the LENGTH against an exact `when` for
   the START: a video frame is 880.591 samples at FRAME_HZ = 50.08, so the
   buffer never fills the slot it was cut for and consecutive buffers either
   overlap (and SUM) or leave a hole. */
function unfix(out, FRAME_HZ) {
  out.flushBeeper = function (events, upto) {
    if (!this.ctx || !this.on) { events.length = 0; return; }
    const now = this.ctx.currentTime;
    this.live = this.live.filter(t => t > now);
    if (this.next === null) {
      this.next = upto; this.base = upto; this.t0 = now + 0.08;
      events.length = 0; return;
    }
    const f0 = this.next, nf = upto - f0;
    if (nf <= 0) return;
    let when = this.t0 + (f0 - this.base) / FRAME_HZ;
    if (when < now + 0.005 || when > now + 3.0) {
      this.base = f0; this.t0 = when = now + 0.08; this.resyncs++;
    }
    let cut = 0;
    while (cut < events.length && events[cut][0] < upto) cut++;
    if (this.live.length >= 48) {
      this.dropped++; this.next = upto;
      if (cut) this.chip.lvl = events[cut - 1][1];
      events.splice(0, cut);
      return;
    }
    const sr = this.ctx.sampleRate, spf = sr / FRAME_HZ;
    const total = Math.round(nf * spf);
    if (total <= 0) return;
    const buf = this.ctx.createBuffer(1, total, sr);
    const o = buf.getChannelData(0);
    const ed = [];
    for (let i = 0; i < cut; i++)
      ed.push([(events[i][0] - f0) / FRAME_HZ, events[i][1]]);
    this.chip.render(o, 0, total, 0, ed, 0);
    events.splice(0, cut);
    this.next = upto;
    const src = this.ctx.createBufferSource();
    src.buffer = buf; src.connect(this.gain); src.start(when);
    this.live.push(when + total / sr);
  };
  out.flush = function (ev, upto) { return this.flushBeeper(ev, upto); };
}

/* ---------- mixing and writing ----------------------------------------- */
function mix(rec) {
  let end = 0;
  for (const r of rec) end = Math.max(end, r.when + r.data.length / SR);
  const n = Math.ceil(end * SR) + (SR / 10 | 0);
  const m = new Float32Array(n);
  for (const r of rec) {
    const off = Math.round(r.when * SR);
    for (let i = 0; i < r.data.length; i++)
      if (off + i >= 0 && off + i < n) m[off + i] += r.data[i];
  }
  return m;
}
function writeGroup(files, mixes, note) {
  let peak = 0;
  for (const m of mixes) for (let i = 0; i < m.length; i++)
    peak = Math.max(peak, Math.abs(m[i]));
  const g = peak > 0 ? 0.9 / peak : 1;
  files.forEach((f, k) => {
    const a = mixes[k];
    const pcm = Buffer.alloc(a.length * 2);
    for (let i = 0; i < a.length; i++) {
      const v = Math.max(-1, Math.min(1, a[i] * g));
      pcm.writeInt16LE(Math.round(v * 32767), i * 2);
    }
    const h = Buffer.alloc(44);
    h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8);
    h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20);
    h.writeUInt16LE(1, 22); h.writeUInt32LE(SR, 24);
    h.writeUInt32LE(SR * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
    h.write('data', 36); h.writeUInt32LE(pcm.length, 40);
    const p = path.join(ROOT, 'build', f);
    fs.writeFileSync(p, Buffer.concat([h, pcm]));
    console.log('  build/' + f + '   ' + (a.length / SR).toFixed(2) + ' s');
  });
  console.log('  one shared gain x' + g.toFixed(3) + ' across the group -- ' +
              note);
}

/* ---------- 1. the game's own load response ---------------------------- */
/* id 7 (a key) re-armed the instant the driver's step count reaches 0, so the
   chirp train never stops and the interval between chirps IS the pass cost --
   the same rig `python tools/loadsound.py tone` runs on the real Z80. */
function chirpTrain(opt) {
  const h = load();
  const { G, game, out, rec } = h;
  G.seed({});
  if (opt.warp) G.seed({ x: opt.warp[0], y: opt.warp[1],
                         camX: opt.warp[2], camY: opt.warp[3] });
  if (opt.flat) {
    const q = game.quantise.bind(game);
    game.quantise = c => { q(c); game.passFrames = 4; game.passTicks = 4; };
  }
  if (opt.keys) {
    const col = game.x >> 2;
    for (let k = 1; k <= 14; k++) game.map[((game.y >> 2) + k * 2) & 31][col] = 0x19;
  }
  const hist = {};
  const target = game.pass + opt.passes;
  let guard = 0;
  while (game.pass < target && guard++ < 200000) {
    h.adv(DT);
    const p0 = game.pass;
    if (opt.arm && !game.sound.steps) game.sfx(7);
    game.advance(DT, opt.input);
    for (let i = 0; i < game.pass - p0; i++)
      hist[game.passTicks] = (hist[game.passTicks] || 0) + 1;
    out.flush(game.sound.log, game.simFrame);
  }
  for (let i = 0; i < 12; i++) {
    h.adv(DT); game.advance(DT, {}); out.flush(game.sound.log, game.simFrame);
  }
  return { mix: mix(rec), hist };
}

/* ---------- 2. the title tune, through both bridges -------------------- */
function titleTune(old) {
  const h = load();
  const { G, game, out, rec } = h;
  const FRAME_HZ = G.constants.FRAME_HZ;
  if (old) unfix(out, FRAME_HZ);
  const per = [];
  { const c = out.chip, r = c.render.bind(c);
    let st = null;
    c.render = (buf, off, n, t0, ed, ei) => {
      st = { lvl: c.lvl, px: c.px, py: c.py, ed }; return r(buf, off, n, t0, ed, ei);
    };
    const f = out.flush.bind(out);
    out.flush = (ev, upto) => {
      const b0 = rec.length, rv = f(ev, upto);
      if (rec.length > b0 && st) {
        const b = rec[rec.length - 1];
        per.push({ when: b.when, len: b.data.length, st0: st,
                   ed: st.ed.map(e => [e[0] + b.when, e[1]]) });
        st = null;
      }
      return rv;
    }; }
  const frontend = G.frontend.live, kb = new G.frontend.Keyboard();
  let feAcc = 0;
  for (let df = 0; df < 700; df++) {
    h.adv(DT);
    feAcc += DT; if (feAcc > 0.25) feAcc = 0.25;
    while (feAcc >= 1 / FRAME_HZ) {
      feAcc -= 1 / FRAME_HZ; game.simT += 1 / FRAME_HZ;
      const f0 = game.simFrame; game.simFrame++;
      frontend.frame(kb, game.sound.log, f0);
    }
    out.flush(game.sound.log, game.simFrame);
  }
  const m = mix(rec);
  /* THE REFERENCE: the same edges rendered ONCE, continuously, on the same
     sample grid.  m - reference is precisely what the bridge added. */
  const s0 = Math.round(per[0].when * SR);
  const last = per[per.length - 1];
  const n = Math.round(last.when * SR) + last.len - s0;
  const ed = [];
  for (const b of per) for (const e of b.ed) ed.push([e[0] - s0 / SR, e[1]]);
  const chip = new G.sound.BeeperChip(SR);
  chip.lvl = per[0].st0.lvl; chip.px = per[0].st0.px; chip.py = per[0].st0.py;
  const ref = new Float32Array(n);
  chip.render(ref, 0, n, 0, ed, 0);
  const art = new Float32Array(m.length);
  let e2 = 0, s2 = 0;
  for (let i = 0; i < n; i++) {
    art[s0 + i] = m[s0 + i] - ref[i];
    e2 += art[s0 + i] * art[s0 + i]; s2 += ref[i] * ref[i];
  }
  let bad = 0, summed = 0, pk = 0, single = 0;
  for (let i = 0; i + 1 < per.length; i++) {
    const g = Math.round(per[i + 1].when * SR)
            - (Math.round(per[i].when * SR) + per[i].len);
    if (g !== 0) bad++;
    if (g < 0) summed++;
  }
  for (let i = 0; i < m.length; i++) pk = Math.max(pk, Math.abs(m[i]));
  for (const r of rec) for (const v of r.data) single = Math.max(single, Math.abs(v));
  return { mix: m, art, buffers: per.length, bad, summed, pk, single,
           db: 20 * Math.log10(Math.sqrt(e2 / n) / Math.sqrt(s2 / n)) };
}

console.log('\n1. IS THE ROUGHNESS THE GAME\'S?  (effect id 7, one chirp a PASS)');
const quiet = chirpTrain({ input: {}, passes: 48, arm: true });
const clust = chirpTrain({ input: { down: true }, passes: 48, arm: true,
                           warp: [96, 56, 66, 38] });
console.log('   quiet dungeon 1     passTicks ' + JSON.stringify(quiet.hist));
console.log('   generator cluster   passTicks ' + JSON.stringify(clust.hist));
writeGroup(['demo_1_effect_quiet_dungeon.wav',
            'demo_2_effect_generator_cluster.wav'],
           [quiet.mix, clust.mix], 'the same effect, two scenes');

const now = chirpTrain({ input: { down: true }, passes: 64, keys: true });
const was = chirpTrain({ input: { down: true }, passes: 64, keys: true, flat: true });
console.log('   walk, clock AS SHIPPED   passTicks ' + JSON.stringify(now.hist));
console.log('   walk, clock FLAT FOUR    passTicks ' + JSON.stringify(was.hist));
writeGroup(['demo_3_walk_clock_as_shipped.wav',
            'demo_4_walk_clock_flat_four.wav'],
           [now.mix, was.mix], 'the same walk, only the pass clock differs');

console.log('\n2. WAS THERE AN ARTEFACT?  (the front end\'s title tune)');
const before = titleTune(true), after = titleTune(false);
for (const [n2, r] of [['bridge before the fix', before], ['bridge now', after]])
  console.log('   ' + n2.padEnd(22) + r.buffers + ' buffers, ' + r.bad +
              ' joins wrong (' + r.summed + ' SUMMED), mix peak ' +
              r.pk.toFixed(4) + ' vs largest single-buffer sample ' +
              r.single.toFixed(4) + ', injected ' + r.db.toFixed(1) + ' dB');
writeGroup(['demo_5_titletune_bridge_before.wav',
            'demo_6_titletune_bridge_after.wav',
            'demo_7_titletune_the_artefact_alone.wav'],
           [before.mix, after.mix, before.art],
           'demo_7 is (before - one continuous render of the same edges)');
