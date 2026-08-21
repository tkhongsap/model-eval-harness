"""Merge a supplementary ASR pass into a main pass, and record that it happened.

WHY THIS IS NOT `cp`. When a transcription pass loses items to a transport error, the fix is
to re-transcribe those items. The hazard is what that does to the RECORD. `transcribe.py`
writes `_run.json` at the end of every pass, so re-running it against the main arm directory
with `--items ASR-014 ASR-015` replaces a record saying "135 ok, 3 failed" with one saying
"3 ok, 0 failed" -- and the arm then looks like a 3-item run. That exact confusion has already
cost this project once, when a scorecard read `ok` from a 2-item resume record and reported
"0 of 138 calls transcribed".

So the supplementary pass writes to its OWN arm directory, and this merges the transcripts in
while leaving the main `_run.json` as the record of the main pass, plus a
`supplementary_passes` list saying what was added, from where, and why.

WHAT IT REFUSES:
  * overwriting a transcript that already exists in the main pass. A backfill fills gaps; if
    it would replace something, either the wrong items were re-run or the main pass is not
    what it is believed to be.
  * merging an item the main pass never recorded as failed. A file appearing for an item that
    was never missing means the two passes disagree about what happened.
  * a backfill whose run settings differ from the main pass -- a different model, chunk size
    or language would put two incompatible transcription conditions in one arm and nothing
    downstream would be able to tell.

Usage:
    python scripts/merge_asr_backfill.py --pack asr-eval-v3 \\
        --main typhoon-whisper-large-v3 --backfill typhoon-backfill-1 [--check]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Settings that must match between the two passes for the merge to mean anything.
PINNED = ("model", "base_url", "language", "chunk_seconds")


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"MERGE REFUSING: {msg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="merge_asr_backfill")
    ap.add_argument("--pack", type=Path, required=True)
    ap.add_argument("--main", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    pack = args.pack if args.pack.is_absolute() else REPO / args.pack
    main_dir = pack / "hypotheses" / args.main
    back_dir = pack / "hypotheses" / args.backfill
    for d in (main_dir, back_dir):
        if not d.is_dir():
            raise Refused(f"{d} is not a directory")

    main_run = json.loads((main_dir / "_run.json").read_text(encoding="utf-8"))
    back_run = json.loads((back_dir / "_run.json").read_text(encoding="utf-8"))

    for key in PINNED:
        if main_run.get(key) != back_run.get(key):
            raise Refused(
                f"{key} differs: main {main_run.get(key)!r}, backfill {back_run.get(key)!r}. "
                "Merging two transcription conditions into one arm makes the arm "
                "uninterpretable, and nothing downstream could detect it."
            )

    failed = {i["item_id"] for i in main_run.get("items", [])
              if i.get("status") != "ok"}
    incoming = sorted(p.stem for p in back_dir.glob("ASR-*.txt"))
    if not incoming:
        raise Refused(f"{back_dir} holds no transcripts")

    not_failed = [i for i in incoming if i not in failed]
    if not_failed:
        raise Refused(
            f"the backfill carries {not_failed}, which the main pass did not record as "
            "failed. The two passes disagree about what happened; resolve that before "
            "merging."
        )
    clobber = [i for i in incoming if (main_dir / f"{i}.txt").exists()]
    if clobber:
        raise Refused(
            f"{clobber} already exist in the main pass. A backfill fills gaps; if it would "
            "replace a transcript, either the wrong items were re-run or the main pass is "
            "not what it is believed to be."
        )

    still_missing = sorted(failed - set(incoming))

    print(f"main      {args.main}: {main_run.get('ok')} ok, {main_run.get('failed')} failed")
    print(f"backfill  {args.backfill}: {back_run.get('ok')} ok, {back_run.get('failed')} failed")
    print(f"merging   {len(incoming)} transcript(s): {incoming}")
    for key in PINNED:
        print(f"  {key}: {main_run.get(key)!r} (matches)")
    if still_missing:
        print(f"\n  STILL MISSING after this merge: {still_missing}")

    if args.check:
        print("\n--check: nothing written.")
        return 0

    for item in incoming:
        shutil.copy2(back_dir / f"{item}.txt", main_dir / f"{item}.txt")

    # The main record stays the main record. The supplement is recorded beside it.
    passes = main_run.setdefault("supplementary_passes", [])
    passes.append({
        "from_arm": args.backfill,
        "items": incoming,
        "reason": "items lost by the main pass, re-transcribed under identical settings",
        "main_pass_error_for_these": sorted({
            i.get("error", "") for i in main_run.get("items", [])
            if i["item_id"] in incoming
        }),
        "note": "The `ok` and `failed` counts above describe the MAIN pass and are not "
                "edited. Coverage after supplements is len(items) - still_missing.",
        "still_missing_after": still_missing,
    })
    for entry in main_run.get("items", []):
        if entry["item_id"] in incoming:
            entry["status"] = "ok_after_backfill"
            entry["backfilled_from"] = args.backfill
    (main_dir / "_run.json").write_text(
        json.dumps(main_run, ensure_ascii=False, indent=2), encoding="utf-8")

    have = len(list(main_dir.glob("ASR-*.txt")))
    print(f"\nmerged. {args.main} now holds {have} transcripts.")
    print("_run.json keeps the main pass counts and records the supplement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
