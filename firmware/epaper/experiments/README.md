# Experiments

The trail that produced the numbers in [../README.md](../README.md).
None of these is needed to use the firmware. They are kept because each one is
the evidence behind a claim, and because re-running one is the fastest way to
check a claim that later looks wrong.

Each file changes **one** variable from the file above it. That was deliberate:
several earlier conclusions were wrong because two things changed at once.

| File | One change from | What it showed |
|---|---|---|
| `replay_accum.py` | the original demo | Baseline, looping. Motion stops after ~80 frames (4–5 s), abruptly, while the firmware keeps running at 18.4 fps. The "freeze" in the original was only the script ending. |
| `power_probe.py` | `replay_accum` | Re-asserting analog power every 60 frames. **No effect** — it is not the charge pump. |
| `sync_probe.py` | `replay_accum` | `sync_old` False → True. Motion never stops again, at a cost of 5% speed. This is the fix. It also revealed that the image was invisible at that drive time. |
| `drive_probe.py` | `sync_probe` | tpa 1 → 2, frame rate 0xFF → 0x44. 9.4 fps with a clearly visible wave. |
| `shape_probe.py` → `../examples/gradient.py` | `drive_probe` | Folded triangle → one sine period. Single peak. This became the milestone. |
| `groups_probe.py` | `shape_probe` | Groups 3 → 1 at the same drive. 11.5 fps, contrast slightly worse. The hold phases are **not** free. |
| `fps_probe.py` | `shape_probe` | tpa 2 → 1. 11.5 fps. |
| `fps_probe2.py` | `fps_probe` | Groups 3 → 1. 14.9 fps. |
| `fps_probe3.py` | `fps_probe2` | Frame rate 0x44 → 0x88. 17.5 fps, and 0xFF gives no more — the byte saturates. |
| `wave10.py` | `shape_probe` | Hold phases traded for drive: 1 group + 60 ms. Same 9.4 fps, and it looks the same as 3 groups + 40 ms. Contrast follows total panel time, not how the time is split. |
| `wave.py`, `code.py` | — | Earlier versions, superseded. `code.py` is the windowing test with the gate-line count set before power-up. |
| `validate.py` | — | Staged visual check (baseline, checkerboard, motion, ghost). Written before the sync fix; its conclusions predate it. |
| `ladder.py` | — | Drive-time ladder, ~400 ms down to ~10 ms. Written but never run; `compare.py` replaced it. |

## Re-running one

```bash
./firmware/tools/deploy.sh epaper/experiments/sync_probe.py
python3 firmware/tools/monitor.py /dev/cu.usbmodem101 25 passive
```

## What went wrong, and why the one-variable rule exists

Three conclusions in this folder's history were wrong, and each had the same
cause — more than one thing changed between observations.

1. **"Windowing, LUT reuse and frame timing are all dead ends."** True in the
   end, but first measured while a 139 ms charge-pump cycle was being paid on
   every frame and swamping everything else.
2. **"The freeze was charge accumulation."** The original demo was a fixed
   120-frame run that finished and exited. It never failed at all.
3. **"Mode 2 (`0x22=0x0C`) is required for partial refresh."** The fix was the
   old-RAM sync, which was changed at the same time. The working firmware uses
   mode `0x04`.
