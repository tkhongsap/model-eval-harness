"""Approval gate 1: stamp an audio plan's corpus hashes from the rendered bytes.

WHY THIS IS A SCRIPT AND NOT A CHECKLIST ITEM. `retention-e23.plan.json` carries the policy
in prose:

    Every sha256 below that is null is null ON PURPOSE. [...] Each null is stamped exactly
    once, from the rendered bytes, at approval gate 1 (corpus freeze), and the stamped plan
    is what the run executes against. A hash invented now would certify bytes nobody has
    produced; a hash stamped after the first scored call would certify whatever the run
    happened to read.

E23 shows what prose alone achieves. On 2026-08-21 its status is still `draft` and every one
of those hashes is still null -- while 1,002 model calls have been made against it, and its
own gate 1 text reads "No model call in E23 happens before this". Nobody decided to skip the
freeze. There was simply nothing that performed it, and a step with no command behind it is a
step that does not happen.

WHAT THIS REFUSES, and why each refusal is here:

  * stamping a plan whose hashes are already stamped -- "exactly once" is the whole policy,
    and a re-stamp after a run would certify whatever that run read.
  * stamping when the corpus is incomplete, or when the manifest and the audio directory
    disagree about which files exist. A duration change RENAMES a wav, so a stale render
    leaves both the old and the new file; that is how 185 files for 138 calls happened, and
    a hash taken over that set is a hash of a set nobody can pair with a label.
  * stamping when any transcript already exists for this corpus. That means an ASR call was
    made first, and the freeze can no longer be said to precede the run.

Usage:
    python scripts/freeze_corpus.py --plan experiments/retention-e24.plan.json \\
        --pack asr-eval-v3 [--check]

`--check` reports what would be stamped and writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"FREEZE REFUSING: {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def aggregate(paths: list[Path]) -> str:
    """One hash over many files, order-independent of the filesystem.

    Built from (name, content-hash) pairs sorted by name rather than by concatenating bytes,
    so it does not silently depend on directory iteration order -- which differs between
    platforms and would make the same corpus hash differently on CI than on a laptop.
    """
    parts = [f"{p.name}:{sha256_file(p)}" for p in sorted(paths, key=lambda q: q.name)]
    return sha256_bytes("\n".join(parts).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="freeze_corpus")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--pack", type=Path, required=True)
    ap.add_argument("--check", action="store_true",
                    help="report what would be stamped; write nothing")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    plan_path = args.plan if args.plan.is_absolute() else REPO / args.plan
    pack = args.pack if args.pack.is_absolute() else REPO / args.pack
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assets = plan.get("assets") or {}

    declared = str((assets.get("corpus_root") or {}).get("path") or "")
    if declared.rstrip("/") != pack.name:
        raise Refused(
            f"the plan's corpus_root is {declared!r} but --pack is {pack.name!r}. "
            "Stamping a plan from a corpus it does not name is how one experiment ends up "
            "certifying another's bytes."
        )

    audio_dir = pack / "audio"
    gt_dir = pack / "ground-truth"
    manifest_path = pack / "manifest.json"
    for required in (audio_dir, gt_dir, manifest_path):
        if not required.exists():
            raise Refused(f"{required} does not exist; the corpus is not rendered")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_items = (assets.get("corpus_manifest") or {}).get("items")
    if expected_items is not None and len(manifest) != expected_items:
        raise Refused(
            f"manifest has {len(manifest)} items, the plan expects {expected_items}"
        )

    # The audio directory and the manifest must describe the SAME set of files.
    on_disk = {p.name for p in audio_dir.glob("*.wav")}
    in_manifest = {row["filename"] for row in manifest}
    if on_disk != in_manifest:
        only_disk = sorted(on_disk - in_manifest)[:5]
        only_manifest = sorted(in_manifest - on_disk)[:5]
        raise Refused(
            f"audio/ holds {len(on_disk)} wav files and manifest.json names "
            f"{len(in_manifest)}. Only on disk: {only_disk}. Only in manifest: "
            f"{only_manifest}. A duration change renames a file, so a stale render leaves "
            "both -- and a hash over that set certifies a corpus nobody can pair with a "
            "label."
        )

    # Per-item hashes in the manifest must still match the bytes on disk.
    drifted = []
    for row in manifest:
        actual = sha256_file(audio_dir / row["filename"])
        if actual != row["sha256"]:
            drifted.append(row["item_id"])
    if drifted:
        raise Refused(
            f"{len(drifted)} audio files no longer match their manifest sha256 "
            f"(first: {drifted[:5]}). The manifest was written by a different render."
        )

    # No transcript may exist yet -- that would be a model call before the freeze.
    hyps = pack / "hypotheses"
    if hyps.is_dir():
        existing = [d.name for d in hyps.iterdir()
                    if d.is_dir() and any(d.glob("*.txt")) and not d.name.startswith("preflight-")]
        if existing:
            raise Refused(
                f"transcripts already exist under {hyps} for arm(s) {existing}. An ASR call "
                "was made before the freeze, so this stamp could not honestly be said to "
                "precede the run. Move them aside and re-transcribe after stamping, or "
                "stamp a fresh corpus. (Directories named preflight-* are ignored: a single "
                "reachability probe is not the run.)"
            )

    business = gt_dir / "business.csv"
    references = sorted(gt_dir.glob("ASR-*.txt"))
    entities = sorted(gt_dir.glob("ASR-*.entities.json"))
    if not references or not entities:
        raise Refused(f"{gt_dir} has no reference transcripts or entity annotations")

    stamps = {
        ("corpus_manifest", "sha256"): sha256_file(manifest_path),
        ("audio_bytes", "aggregate_sha256"): aggregate(
            [audio_dir / row["filename"] for row in manifest]),
        ("business_ground_truth", "sha256"): sha256_file(business),
        ("reference_transcripts", "aggregate_sha256"): aggregate(references),
        ("entity_annotations", "aggregate_sha256"): aggregate(entities),
    }

    already = [f"{a}.{f}" for (a, f) in stamps if (assets.get(a) or {}).get(f) is not None]
    if already and not args.check:
        raise Refused(
            f"already stamped: {already}. The policy is 'stamped exactly once'. A re-stamp "
            "after a run certifies whatever that run read, which is the failure the policy "
            "exists to prevent. A changed corpus is a new experiment id."
        )

    rows = sum(1 for _ in business.read_text(encoding="utf-8").splitlines()[1:])

    print(f"corpus freeze -- {plan['experiment_id']}  <-  {pack.name}")
    print(f"  audio files      {len(manifest)}  (manifest and directory agree)")
    print(f"  per-item hashes  all {len(manifest)} match the bytes on disk")
    print(f"  ground truth     {rows} rows, {len(references)} references, "
          f"{len(entities)} entity files")
    print("")
    for (asset, field), value in stamps.items():
        print(f"  {asset}.{field}")
        print(f"      {value}")

    if args.check:
        print("\n--check: nothing written.")
        return 0

    for (asset, field), value in stamps.items():
        assets[asset][field] = value
    if (assets.get("business_ground_truth") or {}).get("rows") is None:
        assets["business_ground_truth"]["rows"] = rows
    plan["status"] = "qualified"
    plan.setdefault("approvals_given", []).append({
        "gate": "1 (corpus freeze)",
        "stamped_from": pack.name,
        "audio_items": len(manifest),
        "ground_truth_rows": rows,
        "note": "Stamped from the rendered bytes before the first model call of this "
                "experiment, which is what the stamping policy asks for and what E23 "
                "did not get.",
    })
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    # `relative_to` raises for a path outside the repo, which is every temp-dir plan a test
    # builds -- and would turn a successful stamp into a traceback AFTER the file was
    # written, i.e. the worst possible moment.
    try:
        shown = plan_path.relative_to(REPO)
    except ValueError:
        shown = plan_path
    print(f"\nstamped {shown}; status -> qualified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
