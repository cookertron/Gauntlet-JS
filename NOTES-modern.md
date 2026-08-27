# Things the Z80 could not do

Everything in `NOTES-physics.md` is either the original's behaviour or a
decoration sitting on top of it. This file covers four features that are not
dressing — they are only possible because the port is a deterministic
simulation rather than a re-implementation, and that is the point of them.

## Rewind

Hold **R** (or a shoulder button) and the game runs backwards, up to ten
seconds, at three frames of history per displayed frame. It works during play
and during the death pause, where it cancels the death outright.

A snapshot is taken once per animation frame while playing. The state is small
— about thirty scalars, five ostriches, a duck, a lift — with one bulky part,
the 672-byte tile buffer. That only changes on a pickup, so a snapshot keeps
the **same array reference** as its predecessor until the contents differ; ten
seconds of history usually holds two or three real copies of it.

Two things had to be right:

**The newest snapshot is the present.** It was taken at the end of the last
frame, so restoring it does nothing visible. A step has to discard it and
restore the one before, or the first press appears dead. The first version did
not, and landed one frame late on every rewind.

**Restoring the state is not undoing the death.** `restore()` puts `mode` back
from the snapshot -- which is always `"play"`, because snapshots are only taken
while playing -- so the teardown that followed it, gated on *"are we still
dying?"*, could never fire. The simulation rewound correctly while the OOPS
overlay and the rising ghost stayed on screen. Anything torn down on a rewind
has to be torn down unconditionally; checking a flag one line after overwriting
it is not a check. **Reported by Anthony, not caught by the tests I had.**

**Rewinding is not teleporting.** The test that matters is not "does it get
back to the old state" but "does replaying the same input from there produce
the same future". It does, byte for byte, because nothing in the restore path
leaves anything behind:

```
run 30 frames -> mark
run 90 more   -> later
rewind 90     -> state == mark          exactly
run 90 again  -> state == later         exactly
```

## The Z80 view

A toggle that makes the game name the routine that just fired: `$B34C` when the
farmer teeters on that one-pixel overhang, `$9128` on every ostrich decision,
`$9A21` when corn stalls both clocks. Alongside it, the live values those
routines work on — the jump phase byte at `$7327`, the frame counter at
`$72DC`, the sfx timer at `$7370`, the PRNG index and what it just read, and the
main-loop pass rate in passes per second.

`trace()` is called from inside the simulation, so it costs one boolean test
when the view is off.

The first version kept a recency stack of the last nine events. That is useless
here: `$9128` fires on every ostrich decision and a stack of recent events is
nothing but `$9128`. Keying by routine and ordering by last-seen turns it into a
live profile — walking, climbing and the edge check all stay visible with their
counts, and the PRNG is one row among them saying how many decisions the
ostriches have made.

## Ghosts

Your best run on a level, replayed beside you. Positions are recorded once per
animation frame (they only have to look right) and stored in `localStorage`,
best-by-score with ties broken by frame count.

It has its own toggle (**G**, or the button), deliberately *not* gated on the
Enhanced/Original switch: that one is about how the game's own sprites are
drawn, and someone who turns the ghost on wants to see it either way. The
preference persists.

**The ghost's playback position rewinds with everything else.** It is per-frame
state, so it belongs in the snapshot; without it the ghost carries on running
while you rewind and ends up seconds ahead of where you actually are. Easy to
miss, because the ghost still animates perfectly -- it is just no longer racing
you. **Reported by Anthony.**

The ghost is drawn with a **50% checkerboard dither**. The Spectrum had no alpha
channel; masking every other pixel is exactly how a 1984 artist would have drawn
"not really there", and against the solid yellow farmer it reads instantly.

## When a ghost is saved, and telling the player so

A run is banked **when you finish a level**, and only if it beats your stored
best for that level. Dying saves nothing.

That has to happen in exactly one place. The first version banked inside the
tally's counting loop, so pressing SPACE to skip the tally set the phase
straight to `done` and **silently threw the whole run away** — the one route a
player in a hurry takes every time. Both routes now go through `endTally()`.
**Found by Anthony asking when the data was updated, which was the right
question to ask of a system that never said.**

Saving returns a verdict rather than a boolean — `{kept, score, prev, first}` —
because "did it work" is not what the player needs to know. The LEVEL CLEARED
screen now says one of:

- `GHOST SAVED — race it next time` (first run on that level)
- `NEW BEST GHOST 4,500 · beat 4,000`
- `ghost kept: your best is still 4,500`

Returning a verdict object rather than a boolean has one trap in it, which the
now-removed import path fell into: `const kept = saveGhost(...)` followed by
`kept ? "loaded" : "ignored"` always takes the first branch, because an object
is always truthy. It claimed success even when it had refused.

## Rewind and the recording

These two features touch the same data and the first version had them fighting.
`recordFrame` and `recordInput` were not rewound, so after a rewind the stored
run kept the frames that had just been un-played and then appended the ones
played again — a path with a jump in it — while the input stream stayed stamped
against pass counts that no longer happened. **Reported by Anthony as "the ghost
data gets scrambled", which is exactly what it looked like.**

The root cause was one omission: `passes` was not in `SNAP_KEYS`. It is
simulation state — it is what the recorded input is stamped against — so it has
to unwind like everything else. The snapshot now also carries the recording's
length, and `restore()` cuts `recPos` back to it and drops any input transition
stamped later than the restored pass count.

That leaves the recording internally consistent again. **A rewound run is still
not banked**, and that is a separate decision, not a corruption fix:

- a personal best assembled from retries is a hollow target to race;
- attract mode replays the input as a demo of good play, and a demo stitched
  together from second attempts is a dishonest one.

The run is marked the moment `rewindStep` fires, cleared when a level starts,
and the LEVEL CLEARED screen says `no ghost saved — you rewound this run; your
best is still 4,500`. Silence here would have been the worst of both.

## Ranking a per-level ghost

Ghosts are stored per level. They were ranked on `Game.score`, which is the
**cumulative whole-game total** — so the level-2 ghost's key was really "level
1's points plus level 2's points", and two runs of level 2 entered with
different carry-ins were never comparable.

**Reported by Anthony: "I've beaten my ghost on level 2 but it isn't saving."**
Level 1 is immune because it always starts at zero on every path into it, which
is exactly why the complaint was about level 2 and not level 1.

The practice route makes it permanent rather than luck-dependent: choosing a
level from the dropdown goes through `startOrAdvance`, which zeroes the score
first, so a practice run on level 2 can never out-total a ghost banked
mid-playthrough. Measured through the real chain:

```
RUN A  cleared level 1 for 2,900 then a sloppy 30s level 2
       level-2 leg worth 2,582  ->  banked 5,482   kept
RUN B  practised level 2 from the menu, 8s, three times the pickups
       level-2 leg worth 3,632  ->  banked 3,632   REFUSED
       overlay: "ghost kept: your best is still 5,482"
```

The fault is symmetric, which is why the store also drifted rather than merely
freezing: reverse the order and a **worse** level-2 leg overwrites a better
ghost because it arrived with a carry-in. Both halves are the same line.

The fix rebases the score the way `runT0` already rebased the clock:
`startRecording` records `recScore0 = Game.score`, `finishRecording` banks
`total - recScore0`, and `recordFrame` and the racing line subtract the same
baseline — without that last pair the bar reads the carry-in out as a constant
`+2,900` on a race that is dead level. `endTally` and `saveGhost` are untouched;
one function owns the rule.

**Existing stored ghosts are the same bug wearing a different hat.** v1 records
hold cumulative totals, so against level-relative numbers they become
permanently unbeatable. The key moves to `chuckie.ghosts.v2` and only level 1
migrates across, because level 1 is the one level provably entered at zero on
every path; the rest were never meaningful and are dropped.

Two things this deliberately does **not** fix, both real and both separate:
a death refills the bonus and time clocks (faithful to `$A6FE`/`$A72D`), so a
scrappy clear with a late death is handed up to ~2,900 free points that land in
the level's leg; and the recording runs continuously across a death, so a banked
ghost contains the lives that were lost. The second is self-consistent — your
frame index and the ghost's both count from the level start, so the race stays
fair — but the first can genuinely outrank a deathless run.

## The ghost replayed at the wrong speed

`ghostTick()` was `ghostAt++`, called once per **animation frame**, and
`recordFrame()` sampled once per animation frame too. So playback speed was
(the display's refresh rate) / (the rate it was recorded at). Measured:

```
recorded @60fps, 20.000 simulated seconds
  play @ 30fps -> ghost runs 0.500x
  play @ 60fps -> ghost runs 1.000x
  play @144fps -> ghost runs 2.400x
recorded @144fps, replayed @60fps -> 0.417x
  and at the 5-second mark the ghost sprite is 67 pixels from where it belongs
```

**Asked about by Anthony**, from the symptom rather than the code: *"is there a
difference between the speed the ghost is replayed and how I actually performed,
because visually I beat the ghost but statistically I may not."* There was.

The engine already knew the rule — it is written out at `onePass()` for the
attract-mode input stream, *"recording per frame would tie playback to the frame
rate it was captured at"* — it had simply never been applied to the ghost. The
comment above `recordFrame`, "positions once per animation frame, they only have
to look right", was the wrong idea: they have to look right **at the right
moment**.

The fix samples on a fixed **simulated** cadence, `REC_HZ = 60`, so the index
*is* the timestamp and `ghostAt = floor(runSecs() * REC_HZ)`. No extra number
per frame. Because the index is now derived rather than accumulated, rewind
realigns the ghost for free, and a death stalls both sides together since `simT`
does not advance during the death pause. Measured after: 1.000x at 30, 60 and
144fps against a recording made at any of them, and the same run records the
same number of samples (300 / 301 / 300) whatever the display was doing.

It also fixed a related nonsense in `saveGhost`: the tie-break fell back to
`run.pos.length < old.pos.length`, a count of **animation frames**, so the same
run was quicker or slower than itself depending only on refresh rate. Ties are
broken on the clock or not at all.

## A death was paying the player

This, not the replay speed, is what kept refusing a better run.

`loadLevel` refills BONUS and TIME on a respawn — faithful to `$A72D`, and it
stays — but `startRecording` is skipped on that path, so the refund was never
charged. The tally pays bonus and time into the score, so the banked figure
measured **how long the last life was**, not how well the level was played.
Measured at 20.5 points per second handed back, worth up to 2,890 on level 2:

```
played 15s, 0 deaths, 150 pickups -> banked 2924
played 25s, 2 deaths, 250 pickups -> banked 3104
played 30s, 2 deaths, 250 pickups -> banked 3137   <- sloppier banks MORE
played 50s, 2 deaths, 250 pickups -> banked 2795
```

So a scrappy run that died late set a target no clean run could reach.
`startRecording` now zeroes a `recRefund`, `loadLevel` charges the handed-back
bonus and time to it on the `keepLevel` path, and `finishRecording` subtracts
it. A residue of at most 11 points per death survives — `loadLevel` also resets
the sub-timers, discarding partial progress toward the next decrement — which is
one bonus unit plus one time unit and not worth fractional accounting on an
integer score.

## Saying the same thing twice

Two smaller defects made the above impossible to diagnose from the screen.

**The racing line and the verdict were in different currencies.** `recordFrame`
stored raw pickups, but the verdict is decided after the tally has paid in bonus
and time: the ghost banked 3,137 while the highest value in its own score track
was 250. The bar could read "+0 on it" through an entire race the player then
lost. Both sides now use `projectedScore()` — what the run would bank if the
level ended at this instant — so the track ends exactly on the banked figure,
which a test asserts on a natural completion.

**A refusal named only the incumbent.** Five genuine attempts all produced
`ghost kept: your best is still 3,137` and nothing else. `saveGhost` was already
returning the run's own score and time; they were simply unused. It now reads
`ghost kept — you scored 3,062 in 0:35.38; your best is still 3,137 in 0:20.40`.

And `fmtTime` decided the minute split and the zero-padding on the *unrounded*
seconds, so 9.999952 printed as `0:010.00` and 59.999 as `0:60.00`. It rounds
first now.

Earlier stores are dropped rather than migrated. v1 ranked on the cumulative
score; v2 sampled per animation frame and tracked pickups only. Neither can be
repaired by resampling, and a wrong ghost is worse than none — it is an
unbeatable target that looks legitimate.

## Ranking on the clock

Ghosts are ranked on **time**, with the score breaking a dead heat. That is the
third ranking this feature has had, and the reason is the same each time: the
race you can *see* is a race against the clock. You are either ahead of the
ghost on screen or you are not. Ranking on anything else guarantees the picture
and the verdict will disagree, and they did — twice.

The report that settled it: `you scored 4,050 in 0:20.33; your best is still
4,080 in 0:24.09`. Refused, 3.76 seconds faster, 30 points down. Working exactly
as specified, and the specification was wrong for how the thing is used.

**Investigating that also caught me making the project's own signature mistake.**
I calculated a score ceiling for a 24-second run and declared both figures
impossible. They were not: the bonus and time clocks tick **per main-loop pass**,
and an airborne pass costs 560 T-states against 406, so *being in the air slows
the clocks down*. Measured, 20.25 points/sec standing against 14.80 jumping —
a jumpy route legitimately keeps a couple of hundred more points over the same
wall-clock seconds. Assuming a constant rate is precisely the error that made
falling 1.4x too fast in the early engine, and I made it again on the scoreboard.

### The clock is real time now

It was simulated T-states, chosen so a run timed the same on any machine. Ranking
on it made that the wrong trade: a stopwatch should measure what a stopwatch
measures. So `Game.runWall` accumulates real elapsed `dt` while the level is
live, held on `Game` so rewind, capture and restore all handle it for free.

- The **death pause is charged**. It is real time and it is time you lost, and
  SPACE skips it.
- The **tally is not** — the level is already finished when it starts.
- It uses the same clamped `dt` the simulation consumes, so the clock and the
  simulation stay in lockstep. The ghost is indexed off this clock, and letting
  the two diverge would slide the ghost out of the race. The cost is that a
  browser dropping below ~20fps under-reports slightly, since `dt` is capped at
  50ms. Known, accepted, and it takes a badly struggling machine to matter.

Times are compared in whole **milliseconds**, so a dead heat is a real
possibility rather than a floating-point accident, and displayed to three
decimal places because two good runs of the same level land within hundredths.

### Two things that fell out of the change

Moving the clock into `update()` meant the tests had to drive the per-frame path
rather than raw `onePass()` loops — a real-time clock cannot be advanced by a
loop that knows nothing about time. Routing them through `update()` immediately
exposed two defects the raw loops had been hiding, because `checkDeath()` is only
called from `update()`:

- **`passAcc` was never reset.** A frame buys 58,333 T-states and a pass costs
  406 or 560, so a fraction of a pass carries between frames. Leftover credit
  from a previous level shifts where the 143rd/144th pass falls, and a replay
  starting with a different remainder diverges from the run it is replaying. It
  is cleared with the rest of the run state now.
- **Attract mode gave up the moment the demo died.** `attractStep` stopped on any
  non-play mode. A demo of a real run should play a death out, respawn included,
  because that is what the run did. It now stops when the recorded input is
  spent, at the level clear, or on game over.

And one embarrassment worth recording: several steps of that investigation were
spent chasing a recording that appeared to over-sample by 20%. It was not. A
diagnostic `console.log` divided `recPos.length` by 5 when the samples had become
6 numbers wide. The recording had been exactly one sample per frame the whole
time. **Instrument the instrument.**

## Presenting it

Three changes, all aimed at the same thing — the system used to be invisible
until it wasn't.

**A ghost records the score frame by frame**, not just positions. That is one
extra number per frame and it turns the ghost from something you watch into
something you *race*: a line under the screen reads `Racing your best: 4,500 ·
you are +180 on it`, updated at 8Hz, green when ahead and yellow when behind.

**One Ghosts panel** instead of a bare on/off button. It opens with a table —
level, best score, frames, when it was written — with delete per row, and above
it, in plain words, when ghosts are saved and that dying does not save one.
Everything the player might wonder is answered in the one place, including "has
my run been overwritten".

**Sharing ghosts as codes and links was built, then removed.** For a game that
runs locally it was a panel of machinery in service of something nobody had
asked to do, and it made the panel harder to read for the people using it as
intended. The recording format it needed — input rather than positions — stays,
because that is what attract mode replays. Worth recording as a reminder that
"the data compresses beautifully" is not on its own a reason to ship a feature.

**Ghosts are stamped with a time** and the table shows it, so an overwrite is
visible after the fact and not only in the moment it happens.

**And with a run clock**, shown as its own column and live under the screen
while you play. It measures **simulated** time -- the T-states the Z80 would
have spent -- not wall clock. That matters for two reasons: the recorded input
encodes simulated time, so the same run times the same on any machine; and the
engine clamps `passAcc` after a stall, so a browser dropping frames would
otherwise let a run report as quicker than it was. With the frame rate healthy
the two are the same thing.

The clock also replaces frame count as the tie-break on equal scores. Frames
were only ever a proxy for elapsed time, and one that moves with the display
rate. Score still comes first -- a quicker run at a lower score does not
displace a slower, higher one.

## Input, and the press that starts the game

SPACE both starts a level and jumps. The keydown handler sets `keys.Space` and
then flips the mode, so the first frame of play saw it already held and Harry
leapt as the level opened — the same on a death skip and a tally skip.
**Reported by Anthony.**

Clearing the key on the way in does not fix it: auto-repeat fires `keydown`
again a moment later and sets it straight back. What works is a latch — the
press that started the game does not count as a jump until the control has
actually been **released**. One flag, checked in `K.jump()` and cleared once
`keys.Space`, `pad.jump` and `touch.jump` are all clear, so it covers the
keyboard, the pad and the on-screen button alike.

The latch also means a recording is honest about it: `inputMask()` reads through
`K.jump()`, so a latched press records as not-pressed, which is what the
simulation actually did.

While in there: a controller-only player could move but not start. A fresh press
of any face button now starts the game as well, through the same latch.

## Attract mode

Leave the menu alone for fourteen seconds and the title screen plays your best
run back — not a scripted demo, the actual game playing itself. Same level, same
input, same ostriches, same duck.

This is the determinism paying off, and it is why the input is recorded the way
it is. Input is stored **on change, stamped with the pass counter**, not sampled
per animation frame:

- per frame would tie playback to the frame rate it was captured at, and the
  same demo would drift on a different display;
- passes are the simulation's own clock, so a replay is exact whatever the
  browser is doing. A test replays a 200-frame run at both 143 and 286 passes
  per frame and gets the identical result.

A whole run is a few hundred numbers — a 200-frame run above compressed to six
input changes.

One off-by-one worth remembering: `recordInput()` stamps the pass count
*before* the pass it affects, so on playback a transition at pass N must first
apply on pass N+1. Using `>=` rather than `>` made every run diverge at the
first change of direction and nowhere earlier, which is a confusing symptom
until you see why.

## Input

Keyboard, gamepad and on-screen touch all feed the same five predicates, and
every read of the controls in the engine goes through them — which is what lets
a recording capture a gamepad or a thumb as faithfully as a keyboard.

The touch pad stays hidden until a touch actually happens, so a desktop visitor
never sees it and a phone visitor never has to go looking for a keyboard.
Pointer events rather than touch events, with pointer capture, so a held button
stays held when the finger slides off it.
