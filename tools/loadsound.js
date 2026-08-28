/* loadsound.js -- THE PORT'S HALF OF tools/loadsound.py.
 *
 *   node tools/loadsound.js tone      the PORT's chirp train, quiet vs at a
 *                                     generator cluster -- the counterpart of
 *                                     `python tools/loadsound.py tone`
 *   node tools/loadsound.js bridge [quiet|cluster]
 *                                     does SoundOut turn the driver's exact
 *                                     edge stream into exact audio?  Buffer
 *                                     joins, dropouts, noise floor, clipping
 *   node tools/loadsound.js handover  does the bridge survive feHandover()?
 *   node tools/loadsound.js demo      -> build/beeper_quiet_vs_cluster.wav
 *   node tools/loadsound.js ab        -> build/beeper_ab_*_{variable,flat4}.wav
 *   node tools/loadsound.js all
 *
 * Everything here loads web/gauntlet.html -- THE BUILT ARTIFACT -- and drives
 * the shipped SoundOut through a recording stub, so what is measured is what
 * the page itself would have scheduled (manual 14, and the same rig
 * tools/soundwav.js uses).
 *
 * ===========================================================================
 * WHAT IT ESTABLISHED
 * ===========================================================================
 * THE PORT REPRODUCES THE ORIGINAL'S LOAD RESPONSE.  id 7 in the same two
 * scenes tools/loadsound.py uses: pitch 2873.6 / 1588.0 Hz in both, worst
 * change over six chirps 0.000%; cadence 83.9 -> 139.8 ms; span 423 ->
 * 702 ms.  The cluster figures are IDENTICAL to the real Z80's (139.78 ms,
 * 702.2 ms), so the drag a busy screen puts on an effect is the game's.
 *
 * AND IN A QUIET DUNGEON THE SLOWDOWN CHANGED NOTHING AT ALL: the same
 * scripted session rendered with the shipped variable clock and with
 * quantise() forced back to the flat four frames it charged before is
 * SAMPLE-FOR-SAMPLE IDENTICAL -- 246,959 samples compared, 0 differing,
 * max difference 0 of 32767 (`ab`, then diff the two quiet WAVs).
 *
 * THE BRIDGE IS CLEAN.  Buffer joins slip +0.36 / -0.45 / +0.14 of a sample;
 * 39 whole samples are lost over 106 joins in the quiet scene and 4 over 66
 * at a cluster, and the loudest the signal ever is INSIDE a lost sample is
 * 0.0012 (quiet) / 0.0755 (cluster) of full scale -- because a join lands
 * about two video frames after the last chirp, where the DC blocker has
 * decayed.  Floor between bursts -37 dB with spectral flatness 0.003, i.e. a
 * tone tail and not white noise, and identical in both arms.  Not clipping
 * either: the samples above 0.999 are 48 ISOLATED ones in 48 separate runs,
 * which is what the top of a square wave looks like.
 *
 * ONE REAL DEFECT, and `handover` is what found it: Game.reset() sets
 * simFrame back to 0 while SoundOut.next still holds the frame the FRONT END
 * reached, and flushBeeper returns on `nf <= 0` without scheduling anything.
 * A front end that ran 600 video frames buys 721 display frames -- 12.02
 * seconds -- of complete silence at the start of play.
 */

'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.dirname(__dirname);
const BUILT = path.join(ROOT, 'web', 'gauntlet.html');
const SR = 44100, DT = 1 / 60, CPU_HZ = 3500000, FRAME_T = 69888;

/* ONE BOOT PER RUN, and every boot gets its OWN recorder -- so a mode that
   renders two arms (`ab`) cannot leak the first arm's buffers into the
   second, and a mode that re-seeds mid-session cannot be compared against
   one that does not. */
function boot() {
  const rec = [];
  const state = { t: 0 };
  class Ctx {
    constructor() { this.sampleRate = SR; this.destination = {}; }
    get currentTime() { return state.t; }
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
  function makeEl(id) {
    const s = new Set();
    return { id, _text: '', innerHTML: '',
             get textContent() { return this._text; },
             set textContent(v) { this._text = String(v); },
             getContext() { return ctxStub; },
             classList: { add: c => s.add(c), remove: c => s.delete(c),
                          contains: c => s.has(c) },
             width: 256, height: 192 };
  }
  const els = new Map();
  const sandbox = {
    console, atob: s => Buffer.from(s, 'base64').toString('binary'),
    document: { getElementById(id) { if (!els.has(id)) els.set(id, makeEl(id));
                                     return els.get(id); } },
    addEventListener() {}, requestAnimationFrame() { return 1; },
    AudioContext: Ctx,
    Math, JSON, Uint8Array, Float32Array, Buffer, String, Number, Array,
    Object, Error, Map, Set,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const html = fs.readFileSync(BUILT, 'utf8');
  const jm = html.match(
    /<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
  els.set('assets', Object.assign(makeEl('assets'),
                                  { _text: jm[1].replace(/<\\\//g, '</') }));
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>\s*$/)[1], sandbox,
                  { filename: 'gauntlet.html' });
  const G = sandbox.globalThis.__GAUNTLET__;
  G.sound.setMode(G.sound.SOUND_48K);
  const out = G.sound.out;
  out.start();
  if (!(out.chip instanceof G.sound.BeeperChip))
    out.chip = new G.sound.BeeperChip(SR);
  return { G, game: G.game, out, rec, state };
}

function mixdown(rec) {
  let end = 0;
  for (const r of rec) end = Math.max(end, r.when + r.data.length / SR);
  const total = Math.ceil(end * SR) + SR / 10;
  const mix = new Float32Array(total);
  for (const r of rec) {
    const off = Math.round(r.when * SR);
    for (let i = 0; i < r.data.length; i++)
      if (off + i >= 0 && off + i < total) mix[off + i] += r.data[i];
  }
  return mix;
}

function writeWav(file, mix, peakNorm) {
  let peak = 0;
  for (const v of mix) peak = Math.max(peak, Math.abs(v));
  const g = peakNorm && peak > 0 ? 0.9 / peak : 1;
  const pcm = Buffer.alloc(mix.length * 2);
  for (let i = 0; i < mix.length; i++)
    pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, mix[i] * g)) * 32767),
                     i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8);
  h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20);
  h.writeUInt16LE(1, 22); h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(pcm.length, 40);
  fs.writeFileSync(file, Buffer.concat([h, pcm]));
  return peak;
}


/* ===== 1. THE PORT'S CHIRP TRAIN ===== */
function cmdTone(){
  const B = boot();
  const SCENES = [
    ['quiet dungeon 1, idle', {}, {}],
    ['GENERATOR CLUSTER, walking down', {warp:[96,56,66,38]}, {down:true}],
  ];
  
  function scene(cfg, input, npass, armAt, arm){
    const g = B.G.seed({});
    B.G.sound.reset();
    if (cfg.warp){ const [x,y,cx,cy]=cfg.warp;
                   g.players[0].x=x; g.players[0].y=y; g.camX=cx; g.camY=cy; }
    /* tap the driver's own edge() -- the single place all three mechanisms
       write a speaker edge, and what SoundOut later consumes */
    const tap = [];
    { const d=g.sound, orig=d.edge.bind(d);
      d.edge=(f,lvl,src)=>{ tap.push([f,lvl,src||'tone']); return orig(f,lvl,src); }; }
    g.onePass(input);                                    // align
    const frames = [];
    for (let i=0;i<npass;i++){
      if (i===armAt) arm(g);
      const f0 = g.simFrame;
      g.onePass(input);
      frames.push({f0, cost:g.passFrames, ticks:g.passTicks});
    }
    /* only CHANGES of the speaker bit are edges, exactly as the Z80 side */
    const sorted = tap.slice().sort((a,b)=>a[0]-b[0]);
    const edges=[]; let lvl=0;
    for (const [f,b,s] of sorted){ if (b===lvl) continue; lvl=b; edges.push([f,b,s]); }
    return {frames, edges};
  }
  
  function chirps(edges){
    const te = edges.filter(e=>e[2]==='tone').map(e=>e[0]);
    const out=[]; let cur=[];
    for (const f of te){
      if (cur.length && (f-cur[cur.length-1])*FRAME_T > 8000){ out.push(cur); cur=[]; }
      cur.push(f);
    }
    if (cur.length) out.push(cur);
    return out;
  }
  
  const rows=[];
  console.log('='.repeat(74));
  console.log('THE PORT, id 7 (a KEY) -- a 6-row TONE effect, one row a PASS');
  console.log('='.repeat(74));
  for (const [tag,cfg,input] of SCENES){
    const r = scene(cfg, input, 16, 3, g => g.sfx(7));
    const ch = chirps(r.edges);
    const info = ch.map(c=>{
      const d=[]; for(let i=0;i<c.length-1;i++) d.push((c[i+1]-c[i])*FRAME_T);
      d.sort((a,b)=>a-b);
      const hp = d.length ? d[d.length>>1] : 0;
      return {t:c[0], hp, n:c.length, span:(c[c.length-1]-c[0])};
    });
    const gaps=[]; for(let i=0;i<info.length-1;i++) gaps.push(info[i+1].t-info[i].t);
    const meanPass = r.frames.reduce((a,b)=>a+b.cost,0)/r.frames.length;
    const hist={}; for(const f of r.frames){ const k=Math.round(f.cost); hist[k]=(hist[k]||0)+1; }
    console.log('');
    console.log('  '+tag);
    console.log('    pass cost  '+meanPass.toFixed(3)+' video frames = '+
                (50.08/meanPass).toFixed(2)+' Hz  '+JSON.stringify(hist));
    console.log('    chirp  start (ms)   half T    edges   pitch Hz   gap to next (ms)');
    const t00 = info.length?info[0].t:0;
    info.forEach((v,i)=>{
      const g = i<gaps.length ? (gaps[i]*FRAME_T*1000/CPU_HZ).toFixed(1) : '-';
      console.log('    '+String(i+1).padEnd(6)+
        ((v.t-t00)*FRAME_T*1000/CPU_HZ).toFixed(2).padEnd(12)+
        String(Math.round(v.hp)).padEnd(9)+String(v.n).padEnd(7)+
        (v.hp?(CPU_HZ/(2*v.hp)).toFixed(1):'0').padEnd(10)+g);
    });
    const span = info.length ? (info[info.length-1].t+info[info.length-1].span-info[0].t) : 0;
    console.log('    EFFECT SPAN (first edge to last) = '+
                (span*FRAME_T*1000/CPU_HZ).toFixed(1)+' ms');
    rows.push({tag, meanPass, info, gaps, span:span*FRAME_T*1000/CPU_HZ,
               pitches: info.filter(v=>v.hp).map(v=>CPU_HZ/(2*v.hp))});
  }
  const [a,b]=rows;
  const mean=v=>v.reduce((x,y)=>x+y,0)/v.length;
  console.log('');
  console.log('  ---- WHAT LOAD DOES TO THE PORT -----------------------------');
  console.log('    pass cost      '+a.meanPass.toFixed(3)+' -> '+b.meanPass.toFixed(3)+
              ' frames   '+(100*(b.meanPass/a.meanPass-1)).toFixed(1)+'%');
  const k=Math.min(a.pitches.length,b.pitches.length);
  let worst=0; for(let i=0;i<k;i++) worst=Math.max(worst,Math.abs(b.pitches[i]/a.pitches[i]-1));
  console.log('    CHIRP PITCH    '+a.pitches.slice(0,6).map(p=>p.toFixed(0)).join(' '));
  console.log('                   '+b.pitches.slice(0,6).map(p=>p.toFixed(0)).join(' '));
  console.log('                   worst pitch change over '+k+' chirps: '+
              (worst*100).toFixed(3)+'%');
  console.log('    CHIRP CADENCE  '+(mean(a.gaps)*FRAME_T*1000/CPU_HZ).toFixed(2)+' -> '+
              (mean(b.gaps)*FRAME_T*1000/CPU_HZ).toFixed(2)+' ms between chirps   '+
              (100*(mean(b.gaps)/mean(a.gaps)-1)).toFixed(1)+'%');
  console.log('    EFFECT SPAN    '+a.span.toFixed(1)+' -> '+b.span.toFixed(1)+
              ' ms                   '+(100*(b.span/a.span-1)).toFixed(1)+'%');
}


/* ===== 2. THE BRIDGE ===== */
function cmdBridge(which){
  const B = boot();
  const { game, out, rec, state } = B;
  const FRAME_HZ = B.G.constants.FRAME_HZ;
  /* ---------- capture what the bridge CONSUMED --------------------------- */
  const consumed = [];
  const frameMap = [];
  const realFlush = out.flush.bind(out);
  out.flush = (events, upto) => {
    const before = events.slice();
    realFlush(events, upto);
    const n = before.length - events.length;      // splice(0, cut): a PREFIX
    for (let i=0;i<n;i++) consumed.push(before[i]);
    const last = frameMap[frameMap.length-1];
    if (out.base !== null && out.t0 !== null &&
        (!last || last.base !== out.base || last.t0 !== out.t0))
      frameMap.push({base: out.base, t0: out.t0});
  };
  
  /* ---------- the scene -------------------------------------------------- */
  B.G.seed({});
  if (which === 'cluster'){ game.players[0].x=96; game.players[0].y=56;
                            game.camX=66; game.camY=38; }
  else {
    game.actors = [];
    for (let r=0;r<game.map.length;r++) for (let c=0;c<game.map[r].length;c++)
      if (game.map[r][c]>=0x20 && game.map[r][c]<=0x2E) game.map[r][c]=0;
  }
  const input = which==='cluster' ? {down:true} : {};
  
  function displayFrame(){ state.t += DT; game.advance(DT, input);
                           out.flush(game.sound.log, game.simFrame); }
  for (let i=0;i<20;i++) displayFrame();            // settle
  /* fire the same effect repeatedly so there is plenty of signal to compare */
  for (let k=0;k<8;k++){ game.sfx(7); for (let i=0;i<60;i++) displayFrame(); }
  for (let i=0;i<20;i++) displayFrame();
  
  /* ---------- 1. THE BUFFER JOINS, measured directly --------------------- */
  console.log('=== THE BRIDGE, scene: '+which+' ===');
  console.log('buffers scheduled %d   resyncs %d   dropped %d',
              rec.length, out.resyncs, out.dropped);
  let joins = {}, worstGap = 0, nHole = 0;
  for (let i=0;i+1<rec.length;i++){
    const endA = rec[i].when + rec[i].data.length/SR;
    const startB = rec[i+1].when;
    const d = (startB - endA)*SR;                   // in SAMPLES
    const k = d.toFixed(2);
    joins[k] = (joins[k]||0)+1;
    if (Math.abs(d) > Math.abs(worstGap)) worstGap = d;
    /* what a browser actually does: each source starts on a sample boundary */
    const sA = Math.round(rec[i].when*SR) + rec[i].data.length;
    const sB = Math.round(rec[i+1].when*SR);
    if (sB > sA) nHole += (sB - sA);
  }
  console.log('buffer-join gap in SAMPLES (negative = overlap):');
  for (const k of Object.keys(joins).sort((a,b)=>Number(a)-Number(b)))
    console.log('   %s samples  x%d', k.padStart(7), joins[k]);
  console.log('WHOLE SAMPLES LEFT UNWRITTEN once each buffer start is rounded');
  console.log('to a sample boundary (which is what a browser does): %d over ' +
              '%d joins', nHole, rec.length - 1);
  
  /* ---------- 2. mix what was scheduled ---------------------------------- */
  let end = 0;
  for (const r of rec) end = Math.max(end, r.when + r.data.length/SR);
  const total = Math.ceil(end*SR) + 100;
  const mix = new Float32Array(total);
  const written = new Uint8Array(total);
  for (const r of rec){
    const off = Math.round(r.when*SR);
    for (let i=0;i<r.data.length;i++)
      if (off+i>=0 && off+i<total){ mix[off+i]+=r.data[i]; written[off+i]=1; }
  }
  
  /* ---------- 3. render the SAME edges once, continuously ---------------- */
  function frameToTime(f){
    let p = frameMap[0];
    for (const q of frameMap) if (q.base <= f) p = q;
    return p.t0 + (f - p.base)/FRAME_HZ;
  }
  const ideal = new Float32Array(total);
  {
    const chip = new B.G.sound.BeeperChip(SR);
    const ed = consumed.map(([f,lvl]) => [frameToTime(f), lvl])
                       .sort((a,b)=>a[0]-b[0]);
    chip.render(ideal, 0, total, 0, ed, 0);
  }
  
  /* ---------- 4. THE HOLES, and how loud the signal is IN them ----------- */
  /* A sub-sample comparison of mix against ideal is dominated by the +-0.5
     sample quantisation of src.start(when), which for a 21 kHz square is a
     quarter of a period and says nothing audible.  So ask the question that
     IS audible instead: which samples did no buffer write, and what should
     have been there? */
  const t0s = Math.round(frameMap[0].t0*SR);
  let holes=[], run=null;
  for (let i=t0s;i<total-200;i++){
    if (!written[i]){ if(!run) run={a:i,b:i}; else run.b=i; }
    else if (run){ holes.push(run); run=null; }
  }
  if (run) holes.push(run);
  let short=0, shortSamp=0, worstIn=0;
  for (const hgap of holes){
    const len = hgap.b-hgap.a+1;
    if (len > 50) continue;                     // the resync gap, reported apart
    short++; shortSamp += len;
    for (let i=hgap.a;i<=hgap.b;i++)
      worstIn = Math.max(worstIn, Math.abs(ideal[i]));
  }
  console.log('');
  console.log('=== WHAT THE BUFFER JOINS ACTUALLY DO TO THE AUDIO ===');
  console.log('short holes (a join losing whole samples): %d, %d samples total',
              short, shortSamp);
  console.log('LOUDEST the signal ever is inside one of those holes: %s',
              worstIn.toFixed(5));
  console.log('   (full scale is 1.0 -- a hole in silence is inaudible, a hole');
  console.log('    in a chirp is a click)');
  const big = holes.filter(h=>h.b-h.a+1>50);
  console.log('long gaps (>50 samples, i.e. a resync or a genuine stall): %d',
              big.length);
  for (const hgap of big) console.log('   %d samples at t=%ss',
      hgap.b-hgap.a+1, (hgap.a/SR).toFixed(3));
  
  /* ---------- 4b. THE NOISE FLOOR IN THE SILENCE ------------------------- */
  /* "slight noise" is a noise FLOOR.  Take every sample where the ideal
     render is silent to 1e-6 and measure what the scheduled mix has there. */
  let ns=0, nse=0, nsPeak=0;
  for (let i=t0s;i<total-200;i++){
    if (Math.abs(ideal[i]) > 1e-6) continue;
    ns++; nse += mix[i]*mix[i]; nsPeak = Math.max(nsPeak, Math.abs(mix[i]));
  }
  let sig=0, n=0, pm=0, pi=0;
  for (let i=t0s;i<total-200;i++){ sig += ideal[i]*ideal[i]; n++;
    pm=Math.max(pm,Math.abs(mix[i])); pi=Math.max(pi,Math.abs(ideal[i])); }
  console.log('');
  console.log('=== THE NOISE FLOOR ===');
  console.log('peak  mix %s   ideal %s', pm.toFixed(4), pi.toFixed(4));
  console.log('samples the ideal render calls SILENT: %d of %d', ns, n);
  console.log('   what the scheduled mix has there: rms %s, peak %s',
              ns?Math.sqrt(nse/ns).toExponential(3):'0', nsPeak.toExponential(3));
  console.log('   signal rms over the whole run: %s',
              Math.sqrt(sig/n).toExponential(3));
  
  /* ---------- 5. is peak 1.000 clipping? -------------------------------- */
  let atFull=0, runs=0, inRun=false;
  for (let i=t0s;i<total;i++){
    const a = Math.abs(mix[i]);
    if (a > 0.999){ atFull++; if(!inRun){runs++; inRun=true;} } else inRun=false;
  }
  console.log('');
  console.log('samples at |v| > 0.999 : %d in %d runs (a 1-bit square legitimately',
              atFull, runs);
  console.log('   sits at full scale; a CLIPPED one shows long flat runs)');
}


/* ===== 3. THE FRONT-END HANDOVER ===== */
function cmdHandover(FE_FRAMES){
  const B = boot();
  const { game, out, rec, state } = B;
  /* ---- the FRONT END arm of the page's frame(), verbatim in shape ------- */
  for(let i=0;i<FE_FRAMES;i++){
    state.t += 1/B.G.constants.FRAME_HZ;
    game.simFrame++;                                  // template.html line 7975
    out.flush(game.sound.log, game.simFrame);
  }
  console.log('front end ran %d video frames; simFrame=%d, bridge next=%d, '+
              'base=%d', FE_FRAMES, game.simFrame, out.next, out.base);
  
  /* ---- feHandover(): game.reset() puts simFrame back to 0 --------------- */
  game.reset({});
  console.log('after game.reset(): simFrame=%d, bridge next=%d  <-- next is now',
              game.simFrame, out.next);
  console.log('   %d frames AHEAD of the simulation', out.next - game.simFrame);
  
  /* ---- now play, exactly as the page does ------------------------------- */
  const before = rec.length;
  let firstAudio = -1, silentFrames = 0;
  for(let i=0;i<3000;i++){
    state.t += DT;
    game.advance(DT, {down:true});
    if (i % 12 === 0) game.sfx(7);                    // keep the driver busy
    const n0 = rec.length;
    out.flush(game.sound.log, game.simFrame);
    if (rec.length > n0 && firstAudio < 0) firstAudio = i;
    if (firstAudio < 0) silentFrames++;
  }
  console.log('');
  console.log('PLAY: %d display frames driven, effect id 7 fired every 12',
              3000);
  console.log('  first buffer scheduled after the handover: display frame %d',
              firstAudio);
  console.log('  = %s SECONDS OF SILENCE -- one display frame for every',
              (silentFrames*DT).toFixed(2));
  console.log('    video frame the front end ran, because flushBeeper');
  console.log('    returns on nf<=0 and next is never rewound');
  console.log('  buffers scheduled during play: %d', rec.length-before);
  console.log('  resyncs %d  dropped %d', out.resyncs, out.dropped);
}


/* ===== 4. THE WAVs ===== */
/* --------------------------------------------------------------------- */


/* ONE BOOT PER SCENE.  Re-seeding inside a session puts game.simFrame back to
   zero under a bridge whose `next` is still high, and flushBeeper then returns
   on nf<=0 and schedules nothing until the simulation catches up -- a rig
   artefact that would have put a false gap in the middle of the file (and,
   separately, a real defect at feHandover(); see scratchpad/handover.js). */
/* `scene` is 'emptyroom' | 'dungeon' | 'cluster'.
   CORRECTION, and it matters because a figure from this function was quoted
   as proof: what used to be called the "quiet" arm DELETED every actor and
   every generator, which is precisely the scene in which the pass cost
   cannot vary.  Its A/B therefore compared a flat four-frame clock against a
   flat four-frame clock and came out sample-identical BY CONSTRUCTION.  It is
   a useful CONTROL and it is kept, under the name it deserves; 'dungeon' is
   the real quiet-dungeon arm, with the stage left alone. */
function session(flat, scene){
  const cluster = scene === 'cluster';
  const B = boot();
  const {G, game, out, rec, state} = B;
  if (flat){
    /* THE PRE-SLOWDOWN CLOCK, and nothing else changed: quantise() still
       runs (it carries the phase and $8497), then the pass is forced back
       to the flat four frames the engine used to charge. */
    const q = game.quantise.bind(game);
    game.quantise = w1 => { q(w1); game.passFrames = 4; game.passTicks = 4;
      /* hzRing is pushed inside quantise() with the MODEL's cost, so without
         this the flat arm reports the variable arm's rate -- the WAV was
         right and the printed Hz was not. */
      const r = game.hzRing;
      if (r && r.length) r[r.length-1] = 4; };
  }
  function df(input){ state.t += DT; game.advance(DT, input||{});
                      out.flush(game.sound.log, game.simFrame); }
  function passes(n, input){ const tgt=game.pass+n; let g=0;
                             while(game.pass<tgt && g++<100000) df(input); }
  G.seed({});
  if (cluster){ game.players[0].x=96; game.players[0].y=56;
                game.camX=66; game.camY=38; }
  else if (scene === 'emptyroom'){
    game.actors=[];
    for(let r=0;r<game.map.length;r++) for(let c=0;c<game.map[r].length;c++)
      if(game.map[r][c]>=0x20 && game.map[r][c]<=0x2E) game.map[r][c]=0;
  }
  const input = {down:true};
  for (let i=0;i<20;i++) df(input);                    // let the bridge settle
  passes(4, input);
  /* id 7 the KEY (a 6-row warble), id $0E the DOOR (a 5-row fall),
     id $11 an ITEM (4 rows), then id 4 FIRE (the noise ramp) armed at the
     move's own phase, which is what $8CAD does */
  for (const id of [7, 0x0E, 0x11]){ game.sfx(id); passes(10, input); }
  for (let k=0;k<3;k++){ game.passPhase=0.03; game.sfx(4); passes(6, input); }
  passes(6, input);
  for (let i=0;i<20;i++) df(input);
  const hz = 50.08/game.hzAvg();
  return {rec, out, hz, passFrames:game.hzAvg(), mix:mixdown(rec)};
}

function trim(mix){                       // drop the leading dead air
  let a=0; while(a<mix.length && Math.abs(mix[a])<1e-4) a++;
  let b=mix.length; while(b>a && Math.abs(mix[b-1])<1e-4) b--;
  return mix.subarray(Math.max(0,a-2000), Math.min(mix.length,b+4000));
}

function cmdWav(MODE){
 if (MODE === 'demo'){
  /* THREE SEGMENTS, so the owner can separate the two questions himself:
       1  quiet dungeon, as shipped        12.52 Hz
       2  generator cluster, as shipped     8.35 Hz   <- what he is hearing
       3  the SAME cluster with the pass clock forced back to the flat four
          frames it charged before the slowdown, i.e. what it sounded like
          when he approved it.
     1 vs 2 is what the ORIGINAL does under load.  2 vs 3 is what the change
     did.  Nothing else differs between any of them. */
  const segs = [
    ['1  QUIET DUNGEON, as shipped', session(false,false)],
    ['2  GENERATOR CLUSTER, as shipped', session(false,true)],
    ['3  GENERATOR CLUSTER, pre-slowdown flat 4-frame clock',
     session(true,true)],
  ];
  const gap = Math.round(0.9*SR);
  let len = 0;
  const parts = segs.map(([,s]) => trim(s.mix));
  for (const p of parts) len += p.length + gap;
  const outBuf = new Float32Array(len);
  const marks = []; let at = 0;
  parts.forEach((p,i)=>{
    outBuf.set(p, at);
    marks.push({tag:segs[i][0], at:at/SR, seconds:p.length/SR,
                hz:segs[i][1].hz});
    at += p.length + gap;
  });
  const f = path.join(ROOT,'build','beeper_quiet_vs_cluster.wav');
  const peak = writeWav(f, outBuf, true);
  fs.writeFileSync(path.join(ROOT,'build','beeper_quiet_vs_cluster.json'),
    JSON.stringify({sample_rate:SR, marks, peak,
                    what:'the same four effects (id 7 a key, id $0E a door, '+
                         'id $11 an item, then id 4 FIRE three times) played '+
                         'three times: in a quiet dungeon, at a generator '+
                         'cluster, and at the same cluster with the pass '+
                         'clock forced back to a flat four frames.  The '+
                         'chirp PITCHES are identical in all three (609 and '+
                         '1102 T half-periods, 2,873.6 and 1,588.0 Hz); the '+
                         'CADENCE is not.',
                    seconds:outBuf.length/SR}, null, 1));
  console.log(f+'  '+(outBuf.length/SR).toFixed(2)+' s, peak '+peak.toFixed(3));
  for (const m of marks)
    console.log('   '+m.tag+'\n        from '+m.at.toFixed(2)+' s, '+
                m.seconds.toFixed(2)+' s long');
} else {
  for (const tag of ['emptyroom','dungeon','cluster']){
    const v = session(false,tag), fl = session(true,tag);
    for (const [r,name] of [[v,'beeper_ab_'+tag+'_variable'],
                            [fl,'beeper_ab_'+tag+'_flat4']]){
      const f = path.join(ROOT,'build',name+'.wav');
      const peak = writeWav(f, r.mix, false);   // NOT normalised: comparable
      console.log(f+'  '+(r.mix.length/SR).toFixed(2)+' s, '+r.rec.length+
                  ' buffers, peak '+peak.toFixed(3)+', '+r.out.resyncs+
                  ' resync(s), '+r.out.dropped+' dropped, pass '+
                  r.passFrames.toFixed(2)+' f = '+r.hz.toFixed(2)+' Hz');
    }
  }
}
}


const MODE = process.argv[2] || 'all';
const ORDER = ['tone', 'bridge', 'handover', 'demo', 'ab'];
if (MODE !== 'all' && ORDER.indexOf(MODE) < 0) {
  console.log('usage: node tools/loadsound.js [' + ORDER.join('|') + '|all]');
  process.exit(2);
}
for (const k of (MODE === 'all' ? ORDER : [MODE])) {
  if (k === 'tone') cmdTone();
  else if (k === 'bridge') {
    for (const s of (process.argv[3] ? [process.argv[3]]
                                     : ['quiet', 'cluster'])) cmdBridge(s);
  } else if (k === 'handover') cmdHandover(Number(process.argv[3] || 600));
  else cmdWav(k);
  console.log('');
}
