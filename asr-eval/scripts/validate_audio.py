"""Contract checks over the delivered audio set.

This tool answers one question: **would production have accepted these files, and do they
say what the ground truth claims they say?** It re-derives everything from the files on
disk. Where it can, it checks against a re-implementation of production's own parser
(asr_common.parse_filename) rather than against the values the generator held in memory,
so a generator bug cannot validate itself.

Checks are grouped:

  FORMAT     the delivered container matches what production's upload filter accepts
  FILENAME   the ten positional fields survive production's parse, unchanged
  LEVELS     the audio is usable: not silent, not clipped, no DC offset, sane RMS
  DURATION   inside the 3-10 minute band, and consistent with the filename's own field
  CONTENT    the ground truth exists, is non-empty, and its entities are really in it
  SET        coverage across families, directions and durations; no duplicate phone number

Exit code is 1 if any check FAILs, so this is usable as a gate.

Run:
    python asr-eval/scripts/validate_audio.py
    python asr-eval/scripts/validate_audio.py --json reports/validation.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter

import numpy as np
import soundfile as sf
from scipy import signal

import asr_common as C

MIN_DURATION_S = 180.0     # the 3 minutes the set promises
MAX_DURATION_S = 600.0     # the 10 minutes the set promises
MIN_RMS_DBFS = -45.0
MAX_RMS_DBFS = -12.0
MAX_PEAK_DBFS = -0.2       # anything above this is clipping
MAX_DC_OFFSET = 0.01
MAX_SILENCE_RATIO = 0.60
# Syllable-rate band for the speech-presence proxy. Measured 2.0-8.0 Hz is generous;
# all 20 files land between 3.71 and 5.57 Hz.
MIN_MOD_HZ = 2.0
MAX_MOD_HZ = 8.0


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, item: str, group: str, check: str, ok: bool, detail: str = "") -> None:
        self.rows.append({"item": item, "group": group, "check": check,
                          "status": "PASS" if ok else "FAIL", "detail": detail})

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == "FAIL"]


def dbfs(x: float) -> float:
    return 20.0 * np.log10(max(x, 1e-12))


def rel(path) -> str:
    """Path relative to asr-eval/, for anything that goes INTO the report file.

    Never put an absolute path in a committed artifact. The first version stringified
    Path objects straight into the `detail` field, which put the maintainer's OS account
    name and full home-directory layout into validation.json 40 times -- the same leak
    class recorded as a MAJOR finding in this repository on 2026-08-09 (an absolute path
    plus an OS account name in a shareable export). It also made the file unreproducible:
    any other clone regenerating it produced a 40-line diff for reasons unrelated to the
    audio.
    """
    try:
        return str(pathlib.Path(path).resolve().relative_to(C.ROOT))
    except ValueError:
        return pathlib.Path(path).name


def modulation_peak_hz(x: np.ndarray, sr: int) -> float:
    """Dominant frequency of the amplitude envelope, in Hz.

    Real speech modulates at the syllable rate -- the envelope spectrum peaks around
    3-6 Hz across every language studied, Thai included. This is the one check here that
    tests whether the file carries SPEECH rather than merely energy: a file that is all
    hold music, all noise, or a silent buffer with a hum on it passes every level check
    while peaking somewhere else entirely.

    It is a proxy for listening, and it is here because nobody listened to 124 minutes of
    audio before committing it.
    """
    if x.size < sr * 4:
        return float("nan")
    env = np.abs(signal.hilbert(x.astype(np.float64)))
    # Decimate to ~200 Hz, deriving the factor from sr. This was hardcoded to 40, which is
    # right only for the 8 kHz delivery rate; at any other rate the reported frequency was
    # scaled by 200/(sr/40) and silently wrong. It only ever ran on delivered audio, so no
    # published number was affected -- but a test that called it at 24 kHz got 1.37 Hz for a
    # 4 Hz probe, which is how this surfaced.
    factor = max(1, int(round(sr / 200.0)))
    env = signal.decimate(env, factor, ftype="fir")
    env_fs = sr / factor
    env = env - env.mean()
    f, p = signal.welch(env, env_fs, nperseg=min(2048, env.size))
    band = (f >= 1.0) & (f <= 12.0)
    if not band.any():
        return float("nan")
    return float(f[band][np.argmax(p[band])])


def silence_ratio(x: np.ndarray, sr: int, thresh_db: float = -50.0) -> float:
    win = max(1, int(sr * 0.02))
    n = (x.size // win) * win
    if n == 0:
        return 1.0
    frames = x[:n].reshape(-1, win)
    frame_db = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-12)
    return float((frame_db < thresh_db).mean())


def check_file(path, dlg: dict, rep: Report, manifest_row: dict | None) -> dict:
    item = dlg["item_id"]
    name = path.name

    # --- FORMAT ------------------------------------------------------------------------
    info = sf.info(str(path))
    rep.add(item, "FORMAT", "extension is .wav", path.suffix == C.AUDIO_EXT,
            f"got {path.suffix} (production's upload filter accepts .wav only)")
    rep.add(item, "FORMAT", "sample rate 8000", info.samplerate == C.DELIVERY_SAMPLE_RATE,
            f"got {info.samplerate}")
    rep.add(item, "FORMAT", "mono", info.channels == C.DELIVERY_CHANNELS,
            f"got {info.channels} channels")
    rep.add(item, "FORMAT", "16-bit PCM", info.subtype == C.DELIVERY_SUBTYPE,
            f"got {info.subtype}")

    # --- FILENAME ----------------------------------------------------------------------
    parsed = C.parse_filename(name)
    meta = dlg["meta"]
    stem_parts = path.stem.split("_")
    rep.add(item, "FILENAME", "exactly 10 positional tokens",
            len(stem_parts) == C.FILENAME_TOKEN_COUNT,
            f"got {len(stem_parts)}: an extra or missing '_' shifts every later field")
    for field, key in (("call_id", "call_id"), ("phone_number", "phone_number"),
                       ("call_time", "call_time"), ("agent_id", "agent_id"),
                       ("provider", "provider"), ("record_date", "record_date"),
                       ("call_direction", "call_direction")):
        rep.add(item, "FILENAME", f"{field} round-trips", str(parsed[key]) == str(meta[field]),
                f"parsed {parsed[key]!r} vs composed {meta[field]!r}")
    rep.add(item, "FILENAME", "direction is IN or OUT",
            parsed["call_direction"] in C.VALID_DIRECTIONS,
            f"got {parsed['call_direction']!r}; prep_payload_task.py:335 raises on anything else")
    rep.add(item, "FILENAME", "phone inside sanctioned synthetic block",
            bool(C.PHONE_BLOCK_RE.match(str(parsed["phone_number"]))),
            f"got {parsed['phone_number']!r}, expected ^0810000[0-9]{{3}}$")
    rep.add(item, "FILENAME", "record_date passes production regex",
            parsed["record_date"] is not None,
            "production would fall back to the path, then to 99991231")

    # --- audio ------------------------------------------------------------------------
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    duration = x.size / sr
    peak = float(np.abs(x).max()) if x.size else 0.0
    r = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
    dc = float(np.mean(x)) if x.size else 0.0
    sil = silence_ratio(x, sr)

    # --- DURATION ----------------------------------------------------------------------
    rep.add(item, "DURATION", "inside 3-10 minute band",
            MIN_DURATION_S <= duration <= MAX_DURATION_S, f"{duration:.1f}s")
    declared = int(parsed["duration"] or 0)
    rep.add(item, "DURATION", "filename duration matches audio", abs(declared - duration) <= 1.0,
            f"filename says {declared}s, audio is {duration:.1f}s")

    # --- LEVELS ------------------------------------------------------------------------
    rep.add(item, "LEVELS", "not silent", r > 0, "")
    rep.add(item, "LEVELS", "RMS in usable range", MIN_RMS_DBFS <= dbfs(r) <= MAX_RMS_DBFS,
            f"{dbfs(r):.1f} dBFS, expected {MIN_RMS_DBFS}..{MAX_RMS_DBFS}")
    rep.add(item, "LEVELS", "peak not clipping", dbfs(peak) <= MAX_PEAK_DBFS,
            f"peak {dbfs(peak):.2f} dBFS")
    rep.add(item, "LEVELS", "no DC offset", abs(dc) <= MAX_DC_OFFSET, f"mean {dc:+.5f}")
    rep.add(item, "LEVELS", "silence ratio sane", sil <= MAX_SILENCE_RATIO,
            f"{sil:.1%} of frames below -50 dBFS")
    modf = modulation_peak_hz(x, sr)
    rep.add(item, "LEVELS", "envelope modulates at a speech rate",
            MIN_MOD_HZ <= modf <= MAX_MOD_HZ,
            f"envelope peaks at {modf:.2f} Hz, expected {MIN_MOD_HZ}-{MAX_MOD_HZ} Hz "
            f"(the syllable rate); outside this the file may not carry speech at all")

    # --- CONTENT -----------------------------------------------------------------------
    ref_path = C.GROUND_TRUTH_DIR / f"{item}.txt"
    ref = ref_path.read_text(encoding="utf-8") if ref_path.exists() else ""
    rep.add(item, "CONTENT", "reference transcript exists and is non-empty",
            bool(ref.strip()), rel(ref_path))

    ent_path = C.GROUND_TRUTH_DIR / f"{item}.entities.json"
    ents = json.loads(ent_path.read_text(encoding="utf-8")) if ent_path.exists() else []
    norm_ref = C.normalise_thai(ref)
    missing = [e for e in ents if C.normalise_thai(e["spoken"]) not in norm_ref]
    rep.add(item, "CONTENT", "every annotated entity appears in the transcript",
            not missing,
            f"{len(missing)} missing, e.g. {missing[0]['spoken'][:40] if missing else ''!r}")
    rep.add(item, "CONTENT", "has at least one entity", len(ents) > 0, f"{len(ents)} entities")

    tl_path = C.GROUND_TRUTH_DIR / f"{item}.timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.exists() else {}
    segs = tl.get("segments", [])
    rep.add(item, "CONTENT", "timeline exists", bool(segs), rel(tl_path))
    if segs:
        last_end = max(s["start_s"] + s["dur_s"] for s in segs)
        rep.add(item, "CONTENT", "timeline ends within the audio", last_end <= duration + 0.05,
                f"last segment ends {last_end:.2f}s, audio is {duration:.2f}s")
        speech_lines = len([s for s in segs if s["kind"] == "speech"])
        rep.add(item, "CONTENT", "one timeline speech segment per reference line",
                speech_lines == len([ln for ln in ref.splitlines() if ln.strip()]),
                f"{speech_lines} segments vs {len(ref.strip().splitlines())} lines")

    # --- integrity ---------------------------------------------------------------------
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if manifest_row:
        rep.add(item, "FORMAT", "sha256 matches manifest", sha == manifest_row["sha256"],
                "audio changed since the manifest was written")

    return {
        "item_id": item, "family": dlg["family"], "direction": dlg["call_direction"],
        "filename": name, "duration_s": round(duration, 2),
        "rms_dbfs": round(dbfs(r), 2), "peak_dbfs": round(dbfs(peak), 2),
        "dc_offset": round(dc, 6), "silence_ratio": round(sil, 4),
        "modulation_hz": round(modf, 2),
        "entities": len(ents), "sha256": sha, "bytes": path.stat().st_size,
        "phone": parsed["phone_number"],
    }


def check_set(rows: list[dict], rep: Report) -> None:
    """Cross-file properties. A per-file pass says nothing about coverage."""
    fam = Counter(r["family"] for r in rows)
    rep.add("SET", "SET", "all 10 families present", set(fam) == set(C.FAMILIES),
            f"missing {sorted(set(C.FAMILIES) - set(fam))}")
    rep.add("SET", "SET", "every family has >= 2 calls", all(v >= 2 for v in fam.values()),
            str(dict(fam)))

    dirs = Counter(r["direction"] for r in rows)
    rep.add("SET", "SET", "both call directions present", set(dirs) >= {"IN", "OUT"},
            f"{dict(dirs)} -- prep_payload_task.py branches the prompt on this, so a set "
            f"with one direction exercises half of production's prompt surface")

    phones = [r["phone"] for r in rows]
    rep.add("SET", "SET", "phone numbers unique per call", len(set(phones)) == len(phones),
            f"{len(phones) - len(set(phones))} duplicates")
    rep.add("SET", "SET", "all phones inside the reserved sub-range",
            all(C.ASR_PHONE_FIRST <= int(p[-3:]) <= C.ASR_PHONE_LAST for p in phones),
            f"reserved {C.ASR_PHONE_FIRST}-{C.ASR_PHONE_LAST}")

    d = [r["duration_s"] for r in rows]
    rep.add("SET", "SET", "durations span at least 5 minutes", max(d) - min(d) >= 300,
            f"{min(d):.0f}s to {max(d):.0f}s, spread {max(d) - min(d):.0f}s")
    rep.add("SET", "SET", "20 files delivered", len(rows) == 20, f"{len(rows)} files")

    shas = [r["sha256"] for r in rows]
    rep.add("SET", "SET", "no two files are byte-identical", len(set(shas)) == len(shas), "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(C.REPORT_DIR / "validation.json"))
    args = ap.parse_args()

    manifest = {}
    if C.MANIFEST.exists():
        manifest = {r["item_id"]: r for r in json.loads(C.MANIFEST.read_text())}

    rep = Report()
    rows = []
    for dpath in sorted(C.DIALOGUE_DIR.glob("ASR-*.json")):
        dlg = json.loads(dpath.read_text(encoding="utf-8"))
        item = dlg["item_id"]
        matches = sorted(C.AUDIO_DIR.glob(f"*_{dlg['meta']['phone_number']}_*{C.AUDIO_EXT}"))
        if not matches:
            rep.add(item, "FORMAT", "audio file exists", False, "no wav found for this dialogue")
            continue
        rows.append(check_file(matches[0], dlg, rep, manifest.get(item)))

    if rows:
        check_set(rows, rep)

    by_group: dict[str, list[dict]] = {}
    for r in rep.rows:
        by_group.setdefault(r["group"], []).append(r)

    print(f"{'GROUP':10s} {'PASS':>6s} {'FAIL':>6s}")
    print("-" * 26)
    for g, rs in by_group.items():
        p = sum(1 for r in rs if r["status"] == "PASS")
        print(f"{g:10s} {p:6d} {len(rs) - p:6d}")
    print("-" * 26)
    total_fail = len(rep.failures)
    print(f"{'TOTAL':10s} {len(rep.rows) - total_fail:6d} {total_fail:6d}")

    if total_fail:
        print("\nFAILURES")
        for r in rep.failures:
            print(f"  {r['item']:9s} [{r['group']}] {r['check']}: {r['detail']}")

    print("\nPER FILE")
    print(f"{'item':9s} {'family':19s} {'dur':>7s} {'rms':>8s} {'peak':>7s} "
          f"{'sil':>6s} {'mod':>7s} {'ents':>5s}")
    for r in rows:
        print(f"{r['item_id']:9s} {r['family']:19s} {r['duration_s']:6.1f}s "
              f"{r['rms_dbfs']:7.1f}dB {r['peak_dbfs']:6.1f}dB "
              f"{r['silence_ratio']:5.1%} {r['modulation_hz']:6.2f}Hz "
              f"{r['entities']:5d}")

    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"checks": rep.rows, "files": rows,
               "summary": {"pass": len(rep.rows) - total_fail, "fail": total_fail}}
    pathlib.Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.json}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
