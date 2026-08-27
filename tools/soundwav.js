/* soundwav.js -- RENDER THE SHIPPED ENGINE'S AUDIO TO A WAV (manual 14/R9).
 *
 *     node tools/soundwav.js            -> build/sound_effects.wav + .json
 *     node tools/soundwav.js play       -> build/sound_play.wav    + .json
 *
 * "render your audio to a WAV by stubbing the audio API with a recorder and
 *  driving THE SHIPPED CODE PATH, then measure the frequencies back."
 *
 * So this does NOT re-implement anything.  It loads web/gauntlet.html -- the
 * BUILT artifact, not the template -- stubs AudioContext with a recorder, and
 * runs the same loop the browser runs: advance the simulation by a display
 * frame, then hand SoundOut the driver's event log and the simulated frame
 * number.  What lands in the WAV is exactly what the page would have
 * scheduled: the same AyChip samples, at the same AudioContext times, through
 * the same bridge, including its resync and its voice cap.
 *
 * The companion tools/soundwav.py then measures the frequencies back out of
 * the WAV and compares them with clock/(16*TP) computed from the game's own
 * effect data -- the round trip the manual asks for, which is a check on the
 * code that ships rather than on a model agreeing with itself.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.dirname(__dirname);
const BUILT = path.join(ROOT, 'web', 'gauntlet.html');
const SR = 44100;
const DT = 1 / 60;                       // one display frame

/* ---------- the recorder ---------------------------------------------- */
const rec = [];                          // {when, data}
let ctxTime = 0;
class StubAudioContext {
  constructor() {
    this.sampleRate = SR;
    this.destination = { _d: true };
  }
  get currentTime() { return ctxTime; }
  createGain() {
    return { gain: { value: 1 }, connect() {}, _gain: true };
  }
  createBuffer(nch, len) {
    const d = new Float32Array(len);
    return { numberOfChannels: nch, length: len, sampleRate: SR,
             getChannelData() { return d; } };
  }
  createBufferSource() {
    return { buffer: null, connect() {},
             start(when) { rec.push({ when, data: this.buffer.getChannelData() }); } };
  }
}

/* ---------- the DOM stub (the same shape headless.js uses) ------------- */
const ctxStub = { set fillStyle(v) { this._f = v; }, get fillStyle() { return this._f; },
                  fillRect() {} };
function makeEl(id) {
  const set = new Set();
  return { id, _text: '', innerHTML: '',
           get textContent() { return this._text; },
           set textContent(v) { this._text = String(v); },
           getContext() { return ctxStub; },
           classList: { add: c => set.add(c), remove: c => set.delete(c),
                        contains: c => set.has(c) },
           width: 256, height: 192 };
}
const els = new Map();
const sandbox = {
  console,
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  document: { getElementById(id) { if (!els.has(id)) els.set(id, makeEl(id));
                                   return els.get(id); } },
  addEventListener() {},
  requestAnimationFrame() { return 1; },
  AudioContext: StubAudioContext,
  Math, JSON, Uint8Array, Float32Array, Buffer, String, Number, Array, Object,
  Error, Map, Set,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const html = fs.readFileSync(BUILT, 'utf8');
const jsonMatch = html.match(
  /<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
els.set('assets', Object.assign(makeEl('assets'),
                                { _text: jsonMatch[1].replace(/<\\\//g, '</') }));
const codeMatch = html.match(/<script>([\s\S]*?)<\/script>\s*$/);
vm.runInContext(codeMatch[1], sandbox, { filename: 'gauntlet.html' });

const G = sandbox.globalThis.__GAUNTLET__;
const game = G.game;
const out = G.sound.out;
out.start();                              // builds the AyChip on the stub ctx

/* ---------- the driving loop, exactly the page's ---------------------- */
/* THE BRIDGE'S FRAME->TIME MAP IS PIECEWISE, and only the last piece used to
   be recorded.  SoundOut maps simulated frame f to t0 + (f - base)/FRAME_HZ
   and RESYNCS whenever the engine drains more frames at once than the audio
   clock has room for -- which a 72- or 210-frame blocking tune guarantees.
   Recording only the final (t0, base) therefore places everything rendered
   BEFORE the resync at the wrong time: measured, it put the level tune's
   rows two rows (18.3 frames) early and made every pitch read back as its
   neighbour's, which looked like a uniform 29.8% error in the engine and was
   not.  So keep every piece, in order, and let the reader pick. */
const frameMap = [];
function noteMap() {
  const last = frameMap[frameMap.length - 1];
  if (out.base === null || out.t0 === null) return;
  if (!last || last.base !== out.base || last.t0 !== out.t0)
    frameMap.push({ base: out.base, t0: out.t0, at: game.simFrame });
}
function displayFrame(input) {
  ctxTime += DT;
  game.advance(DT, input || {});
  out.flush(game.sound.log, game.simFrame);
  noteMap();
}
function passes(n, input) {
  const target = game.pass + n;
  let guard = 0;
  while (game.pass < target && guard++ < 100000) displayFrame(input);
}

/* MODES.  The first two drive the 128K/AY driver -- verified work, kept and
 * still reachable through the game's own probe flag.  The last three drive
 * the SHIPPED 48K BEEPER.  The output file names say which, because the
 * owner is going to listen to them:
 *     effects   -> build/sound_effects.wav     the AY, 18 ids
 *     play      -> build/sound_play.wav        the AY, a driven session
 *     beep      -> build/beeper_effects.wav    the BEEPER, the 11 live ids
 *     beepplay  -> build/beeper_play.wav       the BEEPER, a driven session
 *     beeptune  -> build/beeper_tune.wav       the two blocking tunes
 *     beeplive  -> build/beeper_live.wav       the BEEPER, on the SHIPPED
 *                                              STAGE -- see the note below
 */
const MODES = { effects: 'sound_effects', play: 'sound_play',
                beep: 'beeper_effects', beepplay: 'beeper_play',
                beeptune: 'beeper_tune', beeplive: 'beeper_live' };
const mode = MODES[process.argv[2]] ? process.argv[2] : 'effects';
const BEEPER = mode.startsWith('beep');
if (BEEPER !== (G.sound.mode() === G.sound.SOUND_48K)) {
  G.sound.setMode(BEEPER ? G.sound.SOUND_48K : G.sound.SOUND_128K);
  out.chip = BEEPER ? new G.sound.BeeperChip(SR)
                    : new G.sound.AyChip(SR, G.sound.AY_CLOCK);
}
const marks = [];
G.seed({});
/* THE STAGE.  Every mode but `beeplive` empties it -- no actors, no
   generators -- so that the only thing audible is the scripted effect, which
   is what a PITCH measurement needs.
   IT IS ALSO A BLIND SPOT, and a play report found it: with the stage empty
   the pass-cost model never fires, every pass costs a flat four video frames,
   and since a beeper effect steps ONE CHIRP PER PASS ($B8FB, one call site at
   $9CD9) these renders cannot observe the game's load response AT ALL.
   Measured: `beep` and `beeptune` come back byte-identical with the clock
   pinned flat, and `beepplay` differs only in the single five-frame banner
   pass.  That is why the round trip and the owner's ears disagreed.
   `beeplive` is the same walk on the dungeon AS SHIPPED, where the model
   fires on about half the passes. */
if (mode !== 'beeplive') {
  game.actors = [];                       // a quiet stage, so only the
  for (let r = 0; r < game.map.length; r++) //   scripted effect is audible
    for (let c = 0; c < game.map[r].length; c++)
      if (game.map[r][c] >= 0x20 && game.map[r][c] <= 0x2E) game.map[r][c] = 0;
}

if (mode === 'effects') {
  /* one effect at a time, with silence between them.  game.sfx() IS the
     port of $BA2B and is what every trigger site calls, so this drives the
     shipped path; the site that would have called it is not the point here.
     Effect 0 is played as both of its two outcomes, because $BAB2's coin
     makes a request for 0 mean 0 OR 1. */
  passes(4);
  for (let id = 0; id < G.sound.SFX_COUNT; id++) {
    const rows = G.sound.streams[id].length;
    if (id <= 1) game.sound.coin = () => id;         // force the coin
    const t0 = game.simFrame;
    game.sfx(id === 1 ? 0 : id);
    const need = Math.ceil((rows + 4) / 4) + 1;      // its frames, in passes
    passes(need);
    marks.push({ id, frame0: t0, frames: rows, rows: G.sound.streams[id] });
    passes(3);                                        // silence between
  }
} else if (mode === 'beep') {
  /* THE SHIPPED DRIVER, one id at a time.  A beeper effect is a train of
     n CHIRPS, one per MAIN-LOOP PASS -- so id 6's nine steps take nine
     passes, not nine video frames, and the audible duty is about 4%.  The
     seven silent ids are played too, so that "silent" is something you can
     hear the absence of rather than a claim in a table. */
  /* THE TWO NOISE IDS ARE RENDERED AT THEIR OWN TRIGGER SITES' PHASES, and
     each of them twice, because WHERE IN THE PASS a noise burst is armed is
     the whole of its duration.  The 127 ramp calls come from the blitter, so
     a burst armed by the player's own move gets the whole blit ahead of it
     and is over in ~10 ms, while one armed by the actor update has to wait
     for the NEXT pass's blit and takes ~85.  Both are real, both are in the
     game, and an earlier write-up reported each of them as "the" duration.
     Site phases measured by `python tools/beepgate.py where`:
        $8509 the moves   0.005..0.052  ->  $8CAD fire (id 4), $A783,
                                            $A63E (id 0)
        $851E the actors  0.723..0.990  ->  $AEFC ghost contact (id 0)   */
  const NOISE_AT = { early: 0.03, late: 0.86 };
  /* SoundOut.flush() CONSUMES the driver's log (events.length = 0), which is
     right -- an event must not be scheduled twice -- so the log cannot be
     read after the fact.  Tap the driver's own edge() instead, which is the
     single place all three mechanisms write a speaker edge. */
  const tap = [];
  { const d = game.sound, orig = d.edge.bind(d);
    d.edge = (f, lvl, src) => { tap.push([f, lvl, src || 'tone']);
                                return orig(f, lvl, src); }; }
  passes(4);
  for (let id = 0; id < 18; id++) {
    const rows = G.sound.beeper.streams[String(id)] || [];
    const noise = (id === 0 || id === 4);
    for (const when of noise ? ['early', 'late'] : ['early']) {
      const t0 = game.simFrame;
      /* $BA2B is reached from inside a pass, so the phase the driver sees is
         the site's, not the loop top's.  Setting it explicitly here is what
         makes the WAV represent what the player hears rather than an
         artefact of driving sfx() from outside onePass(). */
      if (noise) game.passPhase = NOISE_AT[when];
      const logAt = tap.length;
      game.sfx(id);
      const need = (rows.length || 3) + 1;            // one chirp per PASS
      passes(need);
      /* THE DRIVER'S OWN EDGE SPAN, carried alongside the WAV.  A noise
         burst is a sparse random telegraph, so an envelope detector with a
         10 ms gap-merge and a 1.5 ms smoothing window cannot resolve its
         ends -- it is the right tool for a chirp and the wrong one for
         this.  The precise figure is the edge log's, and that is what
         tools/beepgate.py gates against the real Z80; the WAV's envelope is
         reported next to it as the audible confirmation it actually is. */
      const fresh = tap.slice(logAt).filter(r => r[2] === 'noise');
      marks.push({ id, frame0: t0, passes: need, rows,
                   silent: G.sound.beeper.silent.includes(id),
                   noise, armedAt: noise ? NOISE_AT[when] : null,
                   arm: noise ? when : null,
                   edges: fresh.length,
                   logSpanFrames: fresh.length > 1
                                ? fresh[fresh.length - 1][0] - fresh[0][0] : 0,
                   hz: rows.map(([, e]) => G.sound.beepHz(e)) });
      passes(3);                                      // silence between
    }
  }
} else if (mode === 'beeptune') {
  /* THE TWO BLOCKING TUNES.  On this branch they ARE the banner pause:
     $9D0E JR nz is not taken, so $9D1A..$9D34 never runs and $9D10 JPs
     straight into $B8B0 (72.07 frames) or $B8B5 (209.97).  Driven through
     the driver's own tune() so that what is rendered is the port's edge
     stream, not a re-synthesis. */
  passes(2);
  for (const which of ['banner', 'level']) {
    const t0 = game.simFrame;
    const n = game.sound.tune(game.simFrame, which);
    game.sound.endPass(game.simFrame, 0);
    game.simFrame += n;
    marks.push({ id: -1, tune: which, frame0: t0, frames: n,
                 rows: G.sound.beeper.tunes[which].rows });
    for (let i = 0; i < 260; i++) displayFrame({});
    passes(2);
  }
} else if (mode === 'beeplive') {
  /* THE SHIPPED STAGE: dungeon 1 with its actors and its generators, walking
     DOWN a column of planted keys so effect 7 keeps re-triggering.  The pass
     histogram this prints is the point of the mode -- {4:n, 5:m} rather than
     the {4:n} every other scenario produces. */
  const col = game.x >> 2;
  for (let k = 1; k <= 14; k++) game.map[((game.y >> 2) + k * 2) & 31][col] = 0x19;
  const hist = {};
  const t0 = game.simFrame, target = game.pass + 64;
  let guard = 0;
  while (game.pass < target && guard++ < 200000) {
    const p0 = game.pass;
    displayFrame({ down: true });
    for (let i = 0; i < game.pass - p0; i++)
      hist[game.passTicks] = (hist[game.passTicks] || 0) + 1;
  }
  marks.push({ id: -1, frame0: t0, frames: game.simFrame - t0, rows: [],
               passTicks: hist });
  console.log('  pass cost histogram on the SHIPPED stage: ' +
              JSON.stringify(hist));
} else {
  /* a scripted play session: walk DOWN into the key at (3,31), which fires
     effect 7, then on into an inventory item planted in the path, which
     fires 17 -- and on the beeper branch the banner pause IS the blocking
     $B8B0 tune, so it is audible where the AY's 100 frames were silent. */
  const col = game.x >> 2, row = game.y >> 2;
  game.map[(row + 26) & 31][col] = 0x19;
  const t0 = game.simFrame;
  passes(64, { down: true });
  marks.push({ id: -1, frame0: t0, frames: game.simFrame - t0, rows: [] });
}
/* flush the tail */
for (let i = 0; i < 10; i++) displayFrame({});

/* ---------- mix what was SCHEDULED onto one timeline ------------------- */
let end = 0;
for (const r of rec) end = Math.max(end, r.when + r.data.length / SR);
const total = Math.ceil(end * SR) + SR / 10;
const mix = new Float32Array(total);
for (const r of rec) {
  const off = Math.round(r.when * SR);
  for (let i = 0; i < r.data.length; i++)
    if (off + i >= 0 && off + i < total) mix[off + i] += r.data[i];
}
let peak = 0;
for (let i = 0; i < total; i++) peak = Math.max(peak, Math.abs(mix[i]));

/* 16-bit mono WAV */
const pcm = Buffer.alloc(total * 2);
for (let i = 0; i < total; i++) {
  let v = mix[i] * (peak > 0 ? 0.9 / peak : 1);
  v = Math.max(-1, Math.min(1, v));
  pcm.writeInt16LE(Math.round(v * 32767), i * 2);
}
const hdr = Buffer.alloc(44);
hdr.write('RIFF', 0); hdr.writeUInt32LE(36 + pcm.length, 4); hdr.write('WAVE', 8);
hdr.write('fmt ', 12); hdr.writeUInt32LE(16, 16); hdr.writeUInt16LE(1, 20);
hdr.writeUInt16LE(1, 22); hdr.writeUInt32LE(SR, 24); hdr.writeUInt32LE(SR * 2, 28);
hdr.writeUInt16LE(2, 32); hdr.writeUInt16LE(16, 34);
hdr.write('data', 36); hdr.writeUInt32LE(pcm.length, 40);

const base = path.join(ROOT, 'build', MODES[mode]);
fs.writeFileSync(base + '.wav', Buffer.concat([hdr, pcm]));
/* the index the frequency measurement needs: where each effect starts in the
   MIXED timeline.  The bridge maps simulated frame f to
   t0 + (f - base)/FRAME_HZ, and it resyncs, so the honest way to record this
   is to ask the bridge itself. */
fs.writeFileSync(base + '.json', JSON.stringify({
  mode, driver: BEEPER ? 'beeper' : 'ay',
  sample_rate: SR, buffers: rec.length, seconds: total / SR, peak,
  frame_hz: G.constants.FRAME_HZ, ay_clock: G.sound.AY_CLOCK,
  cpu_hz: G.sound.CPU_HZ,
  audio_t0: out.t0, frame_base: out.base, resyncs: out.resyncs,
  frame_map: frameMap,          // every (base, t0) the bridge used, in order
  dropped: out.dropped,
  marks,
}, null, 1));
console.log(`${base}.wav  ${(total / SR).toFixed(2)} s, ${rec.length} scheduled ` +
            `buffers, peak ${peak.toFixed(3)}, ${out.resyncs} resync(s), ` +
            `${out.dropped} dropped`);
