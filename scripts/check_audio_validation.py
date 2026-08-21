"""Gate `validate_audio.py`'s report against an ENUMERATED set of known exceptions.

WHY THIS IS NOT "WIDEN THE THRESHOLD". `validate_audio` asserts, among ~4,149 checks, that a
file's amplitude envelope modulates between 2 and 8 Hz -- the syllable rate -- on the stated
grounds that "outside this the file may not carry speech at all". Two items fail it, and the
naive fixes are both wrong:

  * lowering the band to 1 Hz disables the check for all 138 files and every family, which is
    weakening a gate so a run can pass.
  * ignoring the exit code entirely means the next REAL failure is invisible.

So the threshold does not move and the report is still allowed to say FAIL. What this adds is
that the failure SET is compared against a list written down in advance. Anything not on the
list aborts. Anything on the list that stops failing is also reported, because an exception
nobody needs any more is an exception that should be deleted rather than inherited.

THE EVIDENCE FOR THE TWO ENTRIES BELOW. Both are `far_field_low_gain`, the profile built to
be the hardest acoustic condition in the set (reverb 0.45, wet 0.3, gain -11 dB):

  * they are the two quietest files in the corpus, rms -34.7 and -34.4 dBFS
  * their silence_ratio is 0.005 and 0.003 against a set median near 0.24 -- reverb has filled
    the inter-syllable gaps, which is precisely what reverb does and what this family is for
  * both report EXACTLY 1.07 Hz, which is the detector's lowest bin, not a measurement. The
    envelope has no dominant peak in the speech band, so the estimator falls to the floor
  * the check's own premise is refutable and was refuted: against a 3,513-character reference,
    Qwen3-ASR returned 3,365 characters and Typhoon 3,377 on ASR-049; on ASR-069, 4,035 and
    4,263 against 4,403. Two independent ASR systems transcribed ~96% of the reference length.
    Whatever the envelope says, the files carry speech.
  * they are byte-identical to the same two files in `asr-eval-v2/`, which E23 ran against, so
    they cannot be a difference between E23 and E24

The honest summary is that the modulation heuristic is sound and is mis-specified at the
extreme end of one deliberately extreme profile. That is worth recording, and not worth
disabling a good check over.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (item, check) -> why this one is allowed to fail. Keep the reason with the entry: an
# allowlist whose reasons live in a commit message is an allowlist nobody can re-audit.
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("ASR-049", "envelope modulates at a speech rate"):
        "far_field_low_gain, rms -34.7 dBFS, silence_ratio 0.005. Reverb fills the "
        "inter-syllable gaps so the envelope has no speech-band peak and the estimator "
        "returns its floor bin (1.07 Hz). Qwen3-ASR transcribed 3,365 chars and Typhoon "
        "3,377 against a 3,513-char reference: the file carries speech.",
    ("ASR-069", "envelope modulates at a speech rate"):
        "far_field_low_gain, rms -34.4 dBFS, silence_ratio 0.003. Same mechanism. "
        "Qwen3-ASR 4,035 chars and Typhoon 4,263 against a 4,403-char reference.",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_audio_validation")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--expect-items", type=int, default=None,
                    help="fail if the report covers a different number of files")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    report = json.loads(args.report.read_text(encoding="utf-8"))
    failures = [c for c in report["checks"] if c["status"] != "PASS"]
    files = report.get("files") or []

    print(f"{args.report}")
    print(f"  {report['summary']['pass']} passed, {report['summary']['fail']} failed, "
          f"over {len(files)} files")

    if args.expect_items is not None and len(files) != args.expect_items:
        print(f"\nREFUSING: report covers {len(files)} files, expected "
              f"{args.expect_items}. A validation report for a different corpus than the "
              "one about to be frozen proves nothing about the one about to be frozen.")
        return 1

    unexpected = [c for c in failures if (c["item"], c["check"]) not in KNOWN_EXCEPTIONS]
    accounted = [c for c in failures if (c["item"], c["check"]) in KNOWN_EXCEPTIONS]

    for c in accounted:
        print(f"\n  KNOWN  {c['item']}  {c['check']}")
        print(f"         {KNOWN_EXCEPTIONS[(c['item'], c['check'])]}")

    stale = [k for k in KNOWN_EXCEPTIONS
             if k not in {(c["item"], c["check"]) for c in failures}]
    for item, check in stale:
        print(f"\n  STALE EXCEPTION  {item} / {check!r} no longer fails.")
        print("         Delete the entry. An exception nobody needs is one the next "
              "reader will assume was necessary.")

    if unexpected:
        print(f"\nREFUSING: {len(unexpected)} failure(s) not on the known list:")
        for c in unexpected:
            print(f"  {c['item']}  [{c['group']}]  {c['check']}")
            print(f"      {c['detail']}")
        print("\nThe threshold is not the thing to change. Work out what the audio is "
              "doing, and if it is genuinely acceptable add it here with its evidence.")
        return 1

    print(f"\nOK: {len(failures)} failure(s), all {len(accounted)} accounted for by the "
          "known-exception list. The checks themselves were not relaxed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
