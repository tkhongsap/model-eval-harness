"""Finish a render that free Edge TTS rate-limited partway through.

WHY THIS EXISTS. `synthesize.py` renders a whole pack in one process and gives up on a clip
after four tries (`TTS_RETRIES`, 1.5 s x attempt backoff). Against a free, unauthenticated
service that is the right default for a 20-call pack and the wrong one for 138: sustained use
earns a throttle, the fourth retry lands inside it, and the run dies with 10 calls to go.

Two facts make that recoverable rather than fatal, and this script is built on both:

  * **The TTS cache is global and persists.** A clip fetched in a failed run is still on disk,
    so every subsequent attempt starts closer to done. Observed across three attempts: 3,491
    -> 3,570 cached clips, 126 -> 128 rendered calls. Progress is monotone.
  * **`synthesize.py` takes item ids.** Re-rendering only what is missing costs nothing for
    the calls already done.

So the fix is not more retries inside one process -- it is rounds, with a real pause between
them so the throttle actually expires. A tight retry loop against a rate limiter is how you
stay rate-limited.

WHAT IT DOES NOT DO. It does not lower TTS_CONCURRENCY or touch synthesize.py. The render
that produced 128 of 138 calls used those settings, and changing them mid-pack would mean the
last ten calls were produced under a different configuration from the first 128 -- a
difference no downstream metric could see but which would be real.

    python resume_render.py                 # rounds until complete, or --max-rounds
    python resume_render.py --dry-run       # report what is missing and stop
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import asr_common as C

ROUND_PAUSE_S = 90          # long enough for the throttle to lapse, short enough to finish
DEFAULT_MAX_ROUNDS = 12


def missing_items() -> list[str]:
    """Item ids whose rendered wav is absent OR older than the dialogue it came from.

    Two traps, both hit for real:

      * **Do not match on the whole filename.** `synthesize.py` rewrites the duration field
        with the measured value once the audio exists, so the dialogue's `filename` and the
        delivered file differ by design. Whole-name matching reports every call as missing.
        Matched on the PHONE field instead, which is stable.

      * **Presence is not freshness.** The phone does not change when the SCRIPT does, so a
        pack whose dialogues were regenerated still has a wav for every phone -- and this
        function once reported "138/138 rendered" over audio that spoke the previous
        script. Nothing downstream could have caught it: the transcripts would have been
        faithful transcriptions of the wrong words, scored against the new labels. So a wav
        older than its own dialogue counts as missing.
    """
    rendered: dict[str, Path] = {}
    for wav in C.AUDIO_DIR.glob("*.wav"):
        rendered[wav.name.split("_")[1]] = wav

    out = []
    for path in sorted(C.DIALOGUE_DIR.glob("ASR-*.json")):
        dialogue = json.loads(path.read_text(encoding="utf-8"))
        wav = rendered.get(dialogue["filename"].split("_")[1])
        if wav is None or wav.stat().st_mtime < path.stat().st_mtime:
            out.append(dialogue["item_id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="resume_render")
    ap.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    ap.add_argument("--pause", type=float, default=ROUND_PAUSE_S)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = len(list(C.DIALOGUE_DIR.glob("ASR-*.json")))
    if not total:
        print(f"no dialogues under {C.DIALOGUE_DIR}; run compose_dialogues.py first")
        return 1

    todo = missing_items()
    print(f"pack {C.ROOT.name}: {total - len(todo)}/{total} rendered, "
          f"{len(list(C.CACHE_DIR.glob('*')))} clips cached")
    if args.dry_run or not todo:
        print("missing:", " ".join(todo) if todo else "nothing -- pack is complete")
        return 0 if not todo else 0

    for rnd in range(1, args.max_rounds + 1):
        todo = missing_items()
        if not todo:
            print(f"\ncomplete: {total}/{total} rendered after {rnd - 1} round(s)")
            return 0
        cached = len(list(C.CACHE_DIR.glob("*")))
        print(f"\n--- round {rnd}/{args.max_rounds}: {len(todo)} missing, "
              f"{cached} clips cached ---", flush=True)

        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "synthesize.py"), *todo],
            cwd=str(Path(__file__).parent),
        )

        after = missing_items()
        gained = len(todo) - len(after)
        print(f"round {rnd}: rendered {gained}, {len(after)} still missing "
              f"(exit {proc.returncode})", flush=True)

        if not after:
            print(f"\ncomplete: {total}/{total} rendered after {rnd} round(s)")
            return 0
        if gained == 0 and rnd >= 3:
            # Three rounds of zero progress is a different failure from a throttle -- a
            # clip the service will never return, a network change, a bad dialogue. Say so
            # instead of burning the remaining rounds pretending it is transient.
            print(f"\nREFUSING to continue: three rounds with no progress. {len(after)} "
                  f"calls are not rate-limited, they are failing for another reason. "
                  f"Still missing: {' '.join(after)}")
            return 1
        if rnd < args.max_rounds:
            print(f"pausing {args.pause:.0f}s so the throttle lapses", flush=True)
            time.sleep(args.pause)

    print(f"\nout of rounds: {len(missing_items())} still missing")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
