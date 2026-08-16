"""Graph tools for eyeballing the audio set before spending a model call on it.

Two outputs:

  reports/plots/<ITEM>.png     per call: waveform with the speaker timeline drawn over it,
                               spectrogram, and a short-term level trace. This is how you
                               SEE that overlap_crosstalk really overlaps, that hold_ivr
                               really has non-speech spans, and that far_field_low_gain is
                               genuinely quieter rather than just labelled that way.

  reports/set-overview.png     the whole set on one page: durations by family, level and
                               noise-floor spread, silence, speech ratio, entity density,
                               and a band-energy profile that shows the ten degradation
                               profiles actually separate.

All labels are Latin on purpose. Matplotlib has no Thai font here, and a chart full of
tofu boxes is worse than a chart that names items by id.

Run:
    python asr-eval/scripts/plot_audio.py            # everything
    python asr-eval/scripts/plot_audio.py ASR-007    # one call
    python asr-eval/scripts/plot_audio.py --overview-only
"""

from __future__ import annotations

import argparse
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches          # noqa: E402
import matplotlib.pyplot as plt                # noqa: E402
import numpy as np                             # noqa: E402
import soundfile as sf                         # noqa: E402
from scipy import signal                       # noqa: E402

import asr_common as C                         # noqa: E402

SPEAKER_COLOR = {"agent": "#2c7fb8", "customer": "#d95f0e", "ivr": "#7a7a7a"}
PLOT_DIR = C.REPORT_DIR / "plots"


def frame_db(x: np.ndarray, sr: int, win_s: float = 0.02):
    win = max(1, int(sr * win_s))
    n = (x.size // win) * win
    if n == 0:
        return np.array([0.0]), np.array([0.0])
    frames = x[:n].reshape(-1, win).astype(np.float64)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-12)
    t = (np.arange(frames.shape[0]) * win + win / 2) / sr
    return t, db


def envelope(x: np.ndarray, sr: int, n_cols: int = 2000):
    """Per-column min/max envelope, so a long waveform draws in constant time."""
    if x.size == 0:
        return np.zeros(1), np.zeros(1), np.zeros(1)
    cols = min(n_cols, x.size)
    per = max(1, x.size // cols)
    n = (x.size // per) * per
    blocks = x[:n].reshape(-1, per)
    lo = blocks.min(axis=1)
    hi = blocks.max(axis=1)
    t = (np.arange(blocks.shape[0]) * per + per / 2) / sr
    return lo, hi, t


def load(path):
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def plot_one(item: str, wav_path, timeline: dict, meta: dict) -> None:
    x, sr = load(wav_path)
    dur = x.size / sr
    t = np.arange(x.size) / sr

    fig, axes = plt.subplots(3, 1, figsize=(15, 9), sharex=True,
                             gridspec_kw={"height_ratios": [2, 3, 2]})
    fig.suptitle(
        f"{item}   family={meta['family']}   scenario={meta['scenario']}   "
        f"{meta['direction']}   {dur:.1f}s   {sr} Hz mono",
        fontsize=12, fontweight="bold",
    )

    # --- waveform, with the speaker timeline drawn as bands ----------------------------
    # Drawn as a per-pixel min/max envelope rather than 4.7 million line vertices. At full
    # resolution a 10-minute file took minutes to render and looked identical: the figure is
    # ~1500 px wide, so all but a couple of thousand of those vertices land on a pixel that
    # is already black.
    ax = axes[0]
    lo, hi, tt_env = envelope(x, sr, n_cols=2000)
    ax.fill_between(tt_env, lo, hi, lw=0, color="#333333")
    ax.set_ylim(-1.02, 1.02)
    ax.set_ylabel("amplitude")
    ax.grid(alpha=0.2)
    for seg in timeline.get("segments", []):
        colour = SPEAKER_COLOR.get(seg["speaker"], "#999999")
        alpha = 0.30 if seg["kind"] == "speech" else 0.18
        ax.axvspan(seg["start_s"], seg["start_s"] + seg["dur_s"], color=colour, alpha=alpha, lw=0)
        if seg.get("overlap_s"):
            # Mark where two voices genuinely coincide -- the point of the family.
            ax.axvline(seg["start_s"], color="#c51b7d", lw=0.9, alpha=0.8)
    for d in timeline.get("dropout_spans", []):
        ax.axvspan(d["start_s"], d["start_s"] + d["dur_s"], color="red", alpha=0.5, lw=0)
    handles = [mpatches.Patch(color=v, alpha=0.4, label=k) for k, v in SPEAKER_COLOR.items()]
    handles.append(mpatches.Patch(color="red", alpha=0.5, label="dropout"))
    handles.append(mpatches.Patch(color="#c51b7d", label="overlap start"))
    ax.legend(handles=handles, loc="upper right", ncol=5, fontsize=8, framealpha=0.9)

    # --- spectrogram -------------------------------------------------------------------
    # imshow, not pcolormesh(shading="gouraud"). Gouraud shading interpolates every quad
    # and is what actually made this script time out; imshow blits a bitmap. The hop is
    # also widened to 128 samples, which still resolves individual syllables at 8 kHz.
    ax = axes[1]
    f, tt, Sxx = signal.spectrogram(x, sr, nperseg=256, noverlap=128)
    ax.imshow(10 * np.log10(Sxx + 1e-12), origin="lower", aspect="auto", cmap="magma",
              extent=(float(tt[0]), float(tt[-1]), float(f[0]), float(f[-1])),
              vmin=-110, vmax=-20, interpolation="nearest")
    ax.set_ylabel("Hz")
    ax.set_ylim(0, sr / 2)
    # The telephone passband the delivery profile imposes. Energy should sit between these.
    ax.axhline(300, color="cyan", lw=0.8, ls="--", alpha=0.7)
    ax.axhline(3400, color="cyan", lw=0.8, ls="--", alpha=0.7)

    # --- level trace -------------------------------------------------------------------
    ax = axes[2]
    ft, fdb = frame_db(x, sr)
    ax.plot(ft, fdb, lw=0.6, color="#1a9850")
    ax.axhline(-50, color="#888888", ls=":", lw=1, label="silence threshold (-50 dBFS)")
    rms_db = 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)
    ax.axhline(rms_db, color="#d73027", ls="--", lw=1, label=f"file RMS {rms_db:.1f} dBFS")
    ax.set_ylim(-100, 0)
    ax.set_ylabel("dBFS")
    ax.set_xlabel("seconds")
    ax.grid(alpha=0.2)
    ax.legend(loc="lower right", fontsize=8)

    ax.set_xlim(0, dur)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOT_DIR / f"{item}.png", dpi=110)
    plt.close(fig)


def band_energy(x: np.ndarray, sr: int) -> dict[str, float]:
    f, p = signal.welch(x, sr, nperseg=512)
    tot = p.sum() + 1e-20
    return {
        "sub300": float(p[f < 300].sum() / tot),
        "band": float(p[(f >= 300) & (f <= 3400)].sum() / tot),
        "above3400": float(p[f > 3400].sum() / tot),
    }


def plot_overview(files: list[dict]) -> None:
    fams = sorted({r["family"] for r in files})
    cmap = plt.get_cmap("tab10")
    colour = {f: cmap(i % 10) for i, f in enumerate(fams)}

    fig, axes = plt.subplots(2, 3, figsize=(19, 10))
    fig.suptitle(
        f"ASR eval set overview  -  {len(files)} calls, "
        f"{sum(r['duration_s'] for r in files) / 60:.1f} min total",
        fontsize=14, fontweight="bold",
    )

    # 1. duration per item
    ax = axes[0][0]
    order = sorted(files, key=lambda r: r["duration_s"])
    ax.barh([r["item_id"] for r in order], [r["duration_s"] for r in order],
            color=[colour[r["family"]] for r in order])
    ax.axvline(180, color="red", ls="--", lw=1)
    ax.axvline(600, color="red", ls="--", lw=1)
    ax.set_xlabel("seconds")
    ax.set_title("duration (red = the 3-10 min band)")
    ax.tick_params(labelsize=7)

    # 2. level vs SNR.
    # Plotted as SNR rather than as a raw noise floor, because a raw floor is misleading
    # here: far_field_low_gain has the LOWEST absolute noise floor in the set purely because
    # the whole file is quiet, which reads as "cleanest" when it is one of the two hardest.
    # SNR = speech RMS - noise floor is the number that separates the profiles.
    ax = axes[0][1]
    for r in files:
        ax.scatter(r["rms_dbfs"], r["rms_dbfs"] - r["noise_floor_dbfs"],
                   color=colour[r["family"]], s=45)
    ax.set_xlabel("speech RMS (dBFS)")
    ax.set_ylabel("estimated SNR (dB)")
    ax.set_title("level vs SNR  (lower-left = hardest)")
    ax.grid(alpha=0.25)

    # 3. silence ratio by family
    ax = axes[0][2]
    data = [[r["silence_ratio"] for r in files if r["family"] == f] for f in fams]
    ax.bar(range(len(fams)), [np.mean(d) for d in data],
           color=[colour[f] for f in fams])
    ax.set_xticks(range(len(fams)))
    ax.set_xticklabels(fams, rotation=40, ha="right", fontsize=7)
    ax.set_ylabel("fraction of frames < -50 dBFS")
    ax.set_title("silence ratio by family")

    # 4. band energy -- proves the delivery profile really is band-limited
    ax = axes[1][0]
    idx = np.arange(len(files))
    order2 = sorted(files, key=lambda r: r["family"])
    ax.bar(idx, [r["band_sub300"] for r in order2], label="< 300 Hz", color="#4575b4")
    ax.bar(idx, [r["band_pass"] for r in order2],
           bottom=[r["band_sub300"] for r in order2], label="300-3400 Hz", color="#91cf60")
    ax.bar(idx, [r["band_above"] for r in order2],
           bottom=[r["band_sub300"] + r["band_pass"] for r in order2],
           label="> 3400 Hz", color="#d73027")
    ax.set_xticks(idx)
    ax.set_xticklabels([r["item_id"].replace("ASR-", "") for r in order2], fontsize=7)
    ax.set_ylabel("share of power")
    ax.set_title("spectral distribution vs the telephone passband")
    ax.legend(fontsize=8)

    # 5. speech ratio and entity density
    ax = axes[1][1]
    for r in files:
        ax.scatter(r["speech_ratio"], r["entities"] / (r["duration_s"] / 60),
                   color=colour[r["family"]], s=45)
    ax.set_xlabel("speech / total duration")
    ax.set_ylabel("entities per minute")
    ax.set_title("talk density vs entity density")
    ax.grid(alpha=0.25)

    # 6. family legend + counts
    ax = axes[1][2]
    ax.axis("off")
    from collections import Counter
    cnt = Counter(r["family"] for r in files)
    handles = [mpatches.Patch(color=colour[f], label=f"{f}  (n={cnt[f]})") for f in fams]
    ax.legend(handles=handles, loc="center", fontsize=10, frameon=False,
              title="mechanism families", title_fontsize=11)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(C.REPORT_DIR / "set-overview.png", dpi=110)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("items", nargs="*")
    ap.add_argument("--overview-only", action="store_true")
    args = ap.parse_args()

    summary = []
    for dpath in sorted(C.DIALOGUE_DIR.glob("ASR-*.json")):
        dlg = json.loads(dpath.read_text(encoding="utf-8"))
        item = dlg["item_id"]
        matches = sorted(C.AUDIO_DIR.glob(f"*_{dlg['meta']['phone_number']}_*{C.AUDIO_EXT}"))
        if not matches:
            continue
        wav = matches[0]
        tlp = C.GROUND_TRUTH_DIR / f"{item}.timeline.json"
        timeline = json.loads(tlp.read_text(encoding="utf-8")) if tlp.exists() else {}
        meta = {"family": dlg["family"], "scenario": dlg["scenario"],
                "direction": dlg["call_direction"]}

        if not args.overview_only and (not args.items or item in args.items):
            plot_one(item, wav, timeline, meta)
            print(f"plotted {item}")

        x, sr = load(wav)
        _, fdb = frame_db(x, sr)
        be = band_energy(x, sr)
        speech = sum(s["dur_s"] for s in timeline.get("segments", []) if s["kind"] == "speech")
        ents = C.GROUND_TRUTH_DIR / f"{item}.entities.json"
        summary.append({
            "item_id": item, "family": dlg["family"],
            "duration_s": x.size / sr,
            "rms_dbfs": float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)),
            "noise_floor_dbfs": float(np.percentile(fdb, 5)),
            "silence_ratio": float((fdb < -50).mean()),
            "speech_ratio": speech / (x.size / sr) if x.size else 0.0,
            "entities": len(json.loads(ents.read_text(encoding="utf-8"))) if ents.exists() else 0,
            "band_sub300": be["sub300"], "band_pass": be["band"], "band_above": be["above3400"],
        })

    if summary:
        plot_overview(summary)
        print(f"\nwrote {C.REPORT_DIR / 'set-overview.png'}")
        if not args.overview_only:
            print(f"wrote {len(summary)} per-call plots to {PLOT_DIR}")


if __name__ == "__main__":
    main()
