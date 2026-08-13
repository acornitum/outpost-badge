# Fast refresh on the SSD1680 e-paper panel

How this badge gets 9.4 frames per second out of a 2.9" e-paper panel, what it
costs, and which of the obvious ideas do not work.

All timings here are measured on this board: RP2354A, CircuitPython 10.2.1,
panel `0290BN800F6HP-DL` (296x128, SSD1680), SPI at 20 MHz, room temperature.
They come from the board's own serial output.

Contrast is a different matter. Every contrast rating below — "good", "slightly
worse", "no visible image" — is a judgement by eye, made from A/B runs at
matched frame rates. No optical instrument was used. The ratings are repeatable
and they were the basis for the settings chosen, but they are not measurements.

## Reference points

| Update type | Time for each frame | Speed |
|---|---|---|
| Factory full refresh | 2.29 s | 0.44 fps |
| Factory partial refresh | 0.617 s | 1.6 fps |
| Partial refresh, charge pump held up | 0.478 s | 2.1 fps |
| **This firmware** | **0.107 s** | **9.4 fps** |

That is 21 times faster than a full refresh and 5.8 times faster than the stock
partial refresh.

## Why e-paper looks slow

The 2.29 s full refresh is not a limit of the pigment. It is a choice in the
waveform. The factory waveform drives every pixel black and white several times
before it sets the final colour. That clears the previous image and it balances
the electric charge on each pixel.

The factory partial refresh already skips those clearing passes and drives only
the pixels that change. That alone is the 2.29 s to 0.617 s step, and it is why
partial refresh exists.

What remains in that 0.617 s is approximately 438 ms of real pigment drive. The
factory picked that number so the image is correct at low temperature, on a weak
unit from the production line, after any previous image, with margin on top. It
cannot know your conditions. You can measure yours.

## The three changes

### 1. Hold the charge pump up (free)

The controller has an internal charge pump that makes the high drive voltages.
It needs approximately 139 ms to raise and lower. A normal driver treats each
update as a complete transaction, so it pays that cost on every frame:

    power up -> load waveform -> send frame -> update -> power down

`arm_partial()` does the setup one time. `frame_nopower()` then runs only the
per-frame part:

    send frame -> update -> copy frame into the old RAM

Result: 0.617 s to 0.478 s. This change touches no voltage and no waveform, so
contrast is unaffected. It is the only large gain that costs nothing.

Cost: the analog supply stays on between frames, so current draw is continuous.
We did not measure it. The code must call `power_off()` when it stops.

### 2. Shorten the waveform (costs contrast)

The custom LUT replaces the factory table. Two fields control the drive time:

    drive time of group 0 = tpa x frame period

- `tpa` is the number of frames in group 0, the phase group that draws.
- The frame-rate byte sets the length of one frame from the controller's
  oscillator.

Groups 1 and 2 are hold phases. They cost 20 ms and they draw nothing, but they
are not free in image terms — see the measurements below.

Result: 0.478 s to 0.107 s. This is where every millisecond of contrast goes.

### 3. Copy each frame into the old RAM (required)

The controller picks each pixel's waveform by comparing the old RAM (`0x26`)
against the new RAM (`0x24`). A pixel whose two values match selects a `VSS`
entry and gets no drive at all.

If the old RAM is left holding an old frame, the controller drives the same
pixels the same direction on every frame. Charge builds one way. **The image
looks good for approximately 80 frames and then all motion stops at once.**

That failure is not visible in timing data. The broken version held a steady
18.4 fps, with normal BUSY times and no timeouts, while the panel stopped
responding. It looked like the power had been cut.

`frame_nopower(..., sync_old=True)` does the copy. It costs approximately 3 ms,
or 5 percent of the frame rate (18.4 fps to 17.4 fps). It is not optional.

The panel recovered completely from this after `recondition.py`, so the damage
observed was not permanent. Do not rely on that.

## Measurements

### Speed against contrast

**These numbers describe one panel, in one set of conditions.** They come from a
single unit at room temperature, judged by eye by one person under their own
lighting. Panel-to-panel variation, temperature, viewing angle and ambient light
all move the point where contrast stops being acceptable. A colder panel needs
more drive for the same result.

Treat the table as a starting point, not as a specification. On another board,
re-run `tools/compare.py`, which runs two settings at matched frame rates so
contrast can be compared without frame rate confusing the judgement, and pick
your own point on the curve. The *shape* of the trade should hold — contrast
follows total panel time — but the acceptable threshold is yours to find.

Every row uses `sync_old=True` and mode `0x04`.

| tpa | Frame rate | Groups | Drive | Panel | Total | Speed | Contrast |
|---|---|---|---|---|---|---|---|
| 2 | 0x44 | 3 | 40 ms | 99.8 ms | 106.9 ms | 9.4 fps | good — the milestone |
| 3 | 0x44 | 1 | 60 ms | 99.8 ms | 106.9 ms | 9.4 fps | the same as above |
| 1 | 0x44 | 3 | 20 ms | 79.8 ms | 87.0 ms | 11.5 fps | slightly worse |
| 2 | 0x44 | 1 | 40 ms | 79.8 ms | 87.0 ms | 11.5 fps | slightly worse |
| 1 | 0x44 | 1 | 20 ms | 59.8 ms | 67.1 ms | 14.9 fps | worse |
| 1 | 0x88 | 1 | 10 ms | 49.8 ms | 57.0 ms | 17.5 fps | no visible image |
| 1 | 0xFF | 1 | 10 ms | 50.4 ms | 57.6 ms | 17.4 fps | no visible image |

Two results follow from this table:

1. **Contrast follows the total panel time.** The split between hold phases and
   drive does not matter. Rows 1 and 2 run at the same speed with the time spent
   in different places, and they look the same. Rows 3 and 4 do the same at
   11.5 fps.
2. **There is no free speed left.** Every step below 99.8 ms costs contrast.

At the bottom row the animation still runs. The pigment simply does not move far
enough to see: the panel shows faint moving lines and no image.

### The fixed cost

Approximately 39.8 ms of every frame is fixed. It does not change with the
waveform, the line count, or anything else we found. That sets a ceiling near
25 fps, and no image survives at that end.

Frame period at 0x44 is 20 ms. The frame-rate byte stops helping at 0x88; 0xFF
gives no more speed.

### Things that do not work

| Idea | Result |
|---|---|
| Drive fewer gate lines (a window) | **nothing.** 296 lines 0.05048 s, 32 lines 0.05109 s |
| Reuse the loaded LUT (`0x22=0xC7`) | **nothing** |
| Frame-timing registers `0x3A` / `0x3B` | **nothing**, with either LUT |
| Waveshare `WF_PARTIAL` as published | 0.726 s — *slower*; the per-frame LUT reload costs more than it saves |

The window result is the important one, and it is not an oversight. The waveform
runs as a series of LUT frames whose length comes from the controller's
oscillator. That length does not depend on the gate-line count, so fewer lines
means the scan finishes early and then waits.

**A game cannot save time by updating a small region on this panel.** Plan for a
whole-screen redraw every frame.

## Two defects worth not repeating

### A full refresh after `arm_partial()` is weak

`arm_partial()` writes the custom LUT into `0x32` and overwrites the voltage
registers `0x03`, `0x04` and `0x2C`. Nothing puts the factory table back. Every
later `display_full()` therefore runs the short partial waveform.

Measured: 0.613 s instead of 2.29 s, and almost invisible on the panel.

`epd.init()` does a software reset, which restores the factory table from OTP.
Call it before any full refresh that follows partial-refresh use:

```python
epd.init()
epd.display_full(buf)
```

### Seeding with real content burns it in

`display_base()` is a 2.29 s factory refresh, so whatever it shows is driven at
full strength and leaves the strongest ghost on the panel. Seed it with a blank
background, not with the first frame of content, and let the first partial
update draw the scene.

## The CPU is not the bottleneck

For the animation demos, all frames are built once at start and the loop only
sends bytes:

| Part | Time | Share |
|---|---|---|
| Panel BUSY | 99.7 ms | 93% |
| SPI + Python | 7.2 ms | 7% |

A perfect rewrite in C would give at most 6 percent. The panel owns the frame.

Live drawing does matter, but the fix is to remove repeated work, not to change
language. The first version of `flappy.py` cost 21.4 ms of CPU and ran at
7.8 fps. Two changes brought it to 4.9 ms and 8.9 fps:

- The ground line was redrawn across all 296 rows every frame. It is static, so
  it is now baked into a background buffer that the frame is copied from.
- Every row of a pipe is identical and its gap never changes while it scrolls,
  so the pipe's 16-byte row is built once when it spawns and then assigned.

Both changes stop repeating work. Neither is something C would have fixed.

## Reading the panel geometry

Landscape with the FPC cable to the **left**: the native row axis (296) runs
horizontally and the column axis (128) runs vertically.

- A vertical run on screen is a contiguous bit run inside one panel row. That is
  the cheap drawing primitive.
- A horizontal run on screen touches one bit in many rows. That is the expensive
  one.
- The viewer's left-to-right runs *backwards* along the panel rows on this
  board, which is what `FLIP_X` in `flappy.py` corrects. Note that a coordinate
  flip plus that viewing inversion cancel for a static shape, so text does
  **not** need mirroring on top of `FLIP_X`.

## What this gives up

Against the factory partial refresh, this firmware loses three things:

- **Contrast.** 40 ms of drive instead of 438 ms.
- **Temperature compensation.** The factory table is selected by the controller
  for the measured temperature. The custom table is fixed, so a cold panel will
  look weaker. Untested.
- **A clean state on exit.** The custom table stays loaded until `init()`.

Ghosting also builds up, because the clearing passes are skipped. Run
`recondition.py` when it shows.

One caution on safety. In group 0 the two directions use opposite voltages, so
shortening the drive should scale both equally and keep the per-pixel charge
balanced. That is an argument from the waveform table, not a measurement. We
have no instrument reading that confirms DC balance.

## Dithering

The panel has one bit for each pixel. A 4x4 Bayer pattern gives 17 grey levels.
Grey needs less pigment movement than solid black, so a short waveform suits a
dithered gradient much better than it suits text or solid shapes. A setting that
looks good on a gradient can look weak on a solid black object.
