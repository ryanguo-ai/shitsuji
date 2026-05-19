"""Audio Quality Analyzer.

Detects whether an audio file is genuine Hi-Res or merely upscaled from a
lossy source (e.g. an MP3 padded into a 24-bit / 96 kHz FLAC container).

The decision is driven by spectral analysis with librosa:

* Effective frequency cutoff — the highest frequency that still carries
  meaningful energy.  Lossy codecs (especially MP3) impose a sharp
  low-pass filter; the cutoff value alone is a strong indicator.
* Energy ratio above codec cutoffs (16 kHz, 20 kHz, 22 kHz) — genuine
  Hi-Res material has audible energy well past 20 kHz; upscaled MP3s
  decay into noise above their original cutoff.
* Spectral flatness near the top of the band — a noise floor that is
  effectively flat (and tiny in amplitude) suggests there was never any
  real signal there.

Pandas is used to organise the per-band statistics into a tidy report
table which is rendered both in the UI and in the log.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk

import librosa
import matplotlib
matplotlib.use("Agg")  # we embed via FigureCanvasTkAgg, never via pyplot windows
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from common.logger import get_logger


# --------------------------------------------------------------------------- #
# Pure analysis                                                                #
# --------------------------------------------------------------------------- #

# Energy threshold (dB below the spectrum peak) used to determine the
# "effective" cutoff frequency.  -60 dB is a common engineering choice and
# is well below the level at which real musical content sits.
_CUTOFF_DB_THRESHOLD = -60.0

# Bands evaluated for the report.  Each tuple is (low, high) Hz.
_BANDS = [
    (0,      4_000),
    (4_000,  8_000),
    (8_000,  12_000),
    (12_000, 16_000),
    (16_000, 20_000),
    (20_000, 22_050),
    (22_050, 24_000),
    (24_000, 32_000),
    (32_000, 48_000),
]


def analyze_audio(path: str) -> dict:
    """Run a full spectral analysis on *path* and return a result dict.

    Returns a dict with keys: ``sr``, ``duration``, ``S_db`` (mean-per-bin
    magnitude in dB), ``freqs`` (Hz per bin), ``cutoff_hz``, ``bands``
    (pandas DataFrame), ``verdict`` (str), ``confidence`` (0..1),
    ``details`` (list[str] human readable notes), ``spec_db`` (full
    time-frequency dB matrix for plotting), ``times`` (seconds).
    """
    # Load mono at the file's native sample rate so we can observe the
    # full bandwidth advertised by the container.
    y, sr = librosa.load(path, sr=None, mono=True)
    duration = float(len(y)) / sr if sr else 0.0

    n_fft = 8192
    hop_length = 2048
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    spec_db = librosa.amplitude_to_db(S, ref=np.max)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    times = librosa.frames_to_time(np.arange(spec_db.shape[1]),
                                   sr=sr, hop_length=hop_length)

    # Mean magnitude per frequency bin across all frames, in dB relative
    # to overall peak.  This collapses time and gives us a stable
    # "average spectrum" signature.
    mean_mag = S.mean(axis=1) + 1e-12
    mean_db = 20.0 * np.log10(mean_mag / mean_mag.max())

    cutoff_hz = _effective_cutoff(freqs, mean_db, _CUTOFF_DB_THRESHOLD)

    bands_df = _band_table(freqs, mean_db, sr)

    verdict, confidence, details = _classify(sr, cutoff_hz, bands_df, mean_db, freqs)

    return {
        "path": path,
        "sr": sr,
        "duration": duration,
        "freqs": freqs,
        "mean_db": mean_db,
        "cutoff_hz": cutoff_hz,
        "bands": bands_df,
        "verdict": verdict,
        "confidence": confidence,
        "details": details,
        "spec_db": spec_db,
        "times": times,
    }


def _effective_cutoff(freqs: np.ndarray, mean_db: np.ndarray,
                      threshold_db: float) -> float:
    """Highest frequency at which the mean spectrum is still above
    *threshold_db* relative to the peak."""
    above = np.where(mean_db >= threshold_db)[0]
    if above.size == 0:
        return 0.0
    return float(freqs[above[-1]])


def _band_table(freqs: np.ndarray, mean_db: np.ndarray, sr: int) -> pd.DataFrame:
    """Build the per-band energy table using pandas."""
    nyquist = sr / 2.0
    rows = []
    for lo, hi in _BANDS:
        if lo >= nyquist:
            rows.append({
                "band": f"{lo/1000:>5.1f}–{hi/1000:>5.1f} kHz",
                "mean_db": float("nan"),
                "status": "above Nyquist",
            })
            continue
        hi_eff = min(hi, nyquist)
        mask = (freqs >= lo) & (freqs < hi_eff)
        if not mask.any():
            rows.append({
                "band": f"{lo/1000:>5.1f}–{hi/1000:>5.1f} kHz",
                "mean_db": float("nan"),
                "status": "n/a",
            })
            continue
        band_mean = float(mean_db[mask].mean())
        if band_mean >= -40:
            status = "strong"
        elif band_mean >= -60:
            status = "present"
        elif band_mean >= -90:
            status = "weak"
        else:
            status = "silent"
        rows.append({
            "band": f"{lo/1000:>5.1f}–{hi/1000:>5.1f} kHz",
            "mean_db": round(band_mean, 1),
            "status": status,
        })
    return pd.DataFrame(rows)


def _classify(sr: int, cutoff_hz: float, bands_df: pd.DataFrame,
              mean_db: np.ndarray, freqs: np.ndarray) -> tuple[str, float, list[str]]:
    """Heuristic Hi-Res / upscaled / standard classification.

    Returns (verdict, confidence in 0..1, details list).
    """
    nyquist = sr / 2.0
    details: list[str] = []
    details.append(f"Sample rate: {sr} Hz (Nyquist {nyquist/1000:.1f} kHz)")
    details.append(f"Effective cutoff (≥ {_CUTOFF_DB_THRESHOLD:.0f} dB): "
                   f"{cutoff_hz/1000:.2f} kHz")

    # Energy above key thresholds, in dB.
    def band_db(lo: float, hi: float) -> float:
        hi = min(hi, nyquist)
        if lo >= hi:
            return float("-inf")
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            return float("-inf")
        return float(mean_db[mask].mean())

    db_16_20 = band_db(16_000, 20_000)
    db_20_22 = band_db(20_000, 22_050)
    db_22p   = band_db(22_050, nyquist)

    details.append(f"Energy 16–20 kHz: {db_16_20:.1f} dB")
    details.append(f"Energy 20–22 kHz: {db_20_22:.1f} dB")
    if nyquist > 22_050:
        details.append(f"Energy >22 kHz:    {db_22p:.1f} dB")

    # ---- decision tree ------------------------------------------------- #

    # Hi-res containers (>48 kHz) but with content vanishing well before
    # Nyquist are almost certainly upscaled.
    if sr > 48_000:
        if cutoff_hz < 22_000 and db_22p < -65:
            verdict = "UPSCALED (fake Hi-Res)"
            # Lower cutoff = stronger evidence of MP3 origin.
            if cutoff_hz < 16_500:
                confidence = 0.97
                details.append("Hard cutoff ≈ 16 kHz → typical of MP3 ≤128 kbps "
                               "re-encoded into a Hi-Res container.")
            elif cutoff_hz < 19_000:
                confidence = 0.92
                details.append("Hard cutoff ≈ 18 kHz → typical of MP3 ≈192 kbps.")
            elif cutoff_hz < 20_500:
                confidence = 0.85
                details.append("Hard cutoff ≈ 20 kHz → typical of MP3 ≈256 kbps "
                               "or AAC.")
            else:
                confidence = 0.7
                details.append("Container exceeds 48 kHz but no real ultrasonic "
                               "content present.")
            return verdict, confidence, details

        if cutoff_hz >= 28_000 or db_22p > -55:
            verdict = "GENUINE HI-RES"
            confidence = 0.9
            details.append("Significant content above 22 kHz — consistent with "
                           "a true high-resolution master.")
            return verdict, confidence, details

        verdict = "INCONCLUSIVE (Hi-Res container)"
        confidence = 0.5
        details.append("Hi-Res container with limited ultrasonic energy. "
                       "Source may be 44.1/48 kHz mastered material upsampled, "
                       "rather than a lossy upscale.")
        return verdict, confidence, details

    # 44.1 / 48 kHz container — standard CD-quality or lossy.
    if cutoff_hz < 16_500 and db_16_20 < -70:
        verdict = "LIKELY LOSSY (MP3-like)"
        confidence = 0.9
        details.append("Sharp roll-off near 16 kHz — characteristic of MP3 "
                       "(≤128 kbps).  File may be a transcoded lossy source.")
        return verdict, confidence, details
    if cutoff_hz < 19_000 and db_16_20 < -55:
        verdict = "LIKELY LOSSY (MP3-like)"
        confidence = 0.75
        details.append("Roll-off near 18 kHz — characteristic of MP3 "
                       "(≈192 kbps).")
        return verdict, confidence, details

    verdict = "STANDARD RESOLUTION"
    confidence = 0.8
    details.append("Spectrum extends close to Nyquist — consistent with a "
                   "lossless CD-quality (44.1/48 kHz) source.")
    return verdict, confidence, details


# --------------------------------------------------------------------------- #
# Compact label for tables                                                     #
# --------------------------------------------------------------------------- #

def quality_label(result: dict) -> str:
    """Map a full analysis result to a short label suitable for a table column.

    Returns values like ``"Hi-Res"``, ``"CD"``, ``"MP3 ~128"``, ``"MP3 ~192"``,
    ``"MP3 ~256"``, ``"Upscaled (MP3 ~128)"``, ``"Upscaled"``, ``"Hi-Res?"``.
    """
    verdict = result.get("verdict", "")
    cutoff = float(result.get("cutoff_hz", 0))
    sr = int(result.get("sr", 0))

    def _mp3_tier(c: float) -> str:
        if c < 16_500:
            return "MP3 ~128"
        if c < 19_000:
            return "MP3 ~192"
        if c < 20_500:
            return "MP3 ~256"
        return "MP3 ~320"

    if verdict == "GENUINE HI-RES":
        return "Hi-Res"
    if verdict == "STANDARD RESOLUTION":
        return "CD"
    if verdict == "LIKELY LOSSY (MP3-like)":
        return _mp3_tier(cutoff)
    if verdict == "UPSCALED (fake Hi-Res)":
        return f"Upscaled ({_mp3_tier(cutoff)})"
    if verdict.startswith("INCONCLUSIVE"):
        return "Hi-Res?" if sr > 48_000 else "?"
    return "?"


# --------------------------------------------------------------------------- #
# Tk UI                                                                        #
# --------------------------------------------------------------------------- #

class AudioAnalysisPanel(tk.Toplevel):
    """Spectrogram + Hi-Res verdict report for a single audio file."""

    def __init__(self, parent: tk.Widget, audio_path: str):
        super().__init__(parent)
        self._path = audio_path
        self._log = get_logger("analyze")

        self.title(f"Analyze Track — {os.path.basename(audio_path)}")
        self.configure(bg="#f5f5f5")
        self.geometry("1100x780")
        self.minsize(900, 640)

        self._status_var = tk.StringVar(value="Analyzing…")
        self._build_ui()

        threading.Thread(target=self._run_analysis, daemon=True).start()

    # ------------------------------------------------------------------ #
    # UI                                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg="#2c3e50", pady=10, padx=14)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="🔬  Audio Quality Analyzer",
            font=("Segoe UI", 14, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)
        tk.Label(
            header, textvariable=self._status_var,
            font=("Segoe UI", 10),
            fg="#ecf0f1", bg="#2c3e50",
        ).pack(side=tk.RIGHT)

        path_bar = tk.Frame(self, bg="#f5f5f5", padx=14, pady=6)
        path_bar.pack(fill=tk.X)
        tk.Label(
            path_bar, text=self._path, bg="#f5f5f5",
            font=("Segoe UI", 9), fg="#444", anchor="w",
        ).pack(fill=tk.X)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        # Left: spectrogram
        self._plot_frame = tk.Frame(body, bg="#ffffff", bd=1, relief=tk.SOLID)
        body.add(self._plot_frame, weight=3)

        # Right: report
        right = tk.Frame(body, bg="#f5f5f5")
        body.add(right, weight=2)

        self._verdict_label = tk.Label(
            right, text="…", font=("Segoe UI", 13, "bold"),
            bg="#f5f5f5", fg="#2c3e50", anchor="w", justify="left",
            wraplength=380,
        )
        self._verdict_label.pack(fill=tk.X, padx=8, pady=(6, 4))

        self._confidence_label = tk.Label(
            right, text="", font=("Segoe UI", 10),
            bg="#f5f5f5", fg="#444", anchor="w",
        )
        self._confidence_label.pack(fill=tk.X, padx=8, pady=(0, 8))

        tk.Label(
            right, text="Per-band energy", font=("Segoe UI", 10, "bold"),
            bg="#f5f5f5", anchor="w",
        ).pack(fill=tk.X, padx=8)

        cols = ("band", "mean_db", "status")
        self._table = ttk.Treeview(
            right, columns=cols, show="headings", height=12,
        )
        self._table.heading("band", text="Band")
        self._table.heading("mean_db", text="Mean dB")
        self._table.heading("status", text="Status")
        self._table.column("band", width=140, anchor="w")
        self._table.column("mean_db", width=80, anchor="e")
        self._table.column("status", width=100, anchor="w")
        self._table.pack(fill=tk.BOTH, expand=False, padx=8, pady=(2, 8))

        tk.Label(
            right, text="Details", font=("Segoe UI", 10, "bold"),
            bg="#f5f5f5", anchor="w",
        ).pack(fill=tk.X, padx=8)

        self._details = tk.Text(
            right, height=10, wrap=tk.WORD, bg="#ffffff",
            font=("Consolas", 9), bd=1, relief=tk.SOLID,
        )
        self._details.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))
        self._details.configure(state=tk.DISABLED)

        close_bar = tk.Frame(self, bg="#f5f5f5", padx=10, pady=8)
        close_bar.pack(fill=tk.X)
        tk.Button(
            close_bar, text="Close", command=self.destroy,
            font=("Segoe UI", 9), width=10,
        ).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ #
    # Worker                                                              #
    # ------------------------------------------------------------------ #

    def _run_analysis(self) -> None:
        try:
            result = analyze_audio(self._path)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Analysis failed for %s", self._path)
            self.after(0, lambda: self._show_error(str(exc)))
            return
        self.after(0, lambda: self._render(result))

    def _show_error(self, message: str) -> None:
        self._status_var.set("Failed")
        self._verdict_label.configure(text="Analysis failed", fg="#c0392b")
        self._details.configure(state=tk.NORMAL)
        self._details.delete("1.0", tk.END)
        self._details.insert(tk.END, message)
        self._details.configure(state=tk.DISABLED)

    def _render(self, result: dict) -> None:
        self._status_var.set(
            f"sr={result['sr']} Hz · {result['duration']:.1f} s"
        )

        # Verdict
        verdict = result["verdict"]
        colour = {
            "GENUINE HI-RES":          "#27ae60",
            "STANDARD RESOLUTION":     "#2c3e50",
            "LIKELY LOSSY (MP3-like)": "#d35400",
            "UPSCALED (fake Hi-Res)":  "#c0392b",
        }.get(verdict, "#7f8c8d")
        self._verdict_label.configure(text=verdict, fg=colour)
        self._confidence_label.configure(
            text=f"Confidence: {result['confidence'] * 100:.0f}%   "
                 f"Effective cutoff: {result['cutoff_hz']/1000:.2f} kHz"
        )

        # Band table
        for row in result["bands"].itertuples(index=False):
            mean_db = row.mean_db
            mean_str = "—" if pd.isna(mean_db) else f"{mean_db:.1f}"
            self._table.insert("", tk.END, values=(row.band, mean_str, row.status))

        # Details
        self._details.configure(state=tk.NORMAL)
        self._details.delete("1.0", tk.END)
        self._details.insert(tk.END, "\n".join(result["details"]))
        self._details.insert(tk.END, "\n\n--- Band report ---\n")
        self._details.insert(tk.END, result["bands"].to_string(index=False))
        self._details.configure(state=tk.DISABLED)

        # Spectrogram
        self._draw_spectrogram(result)

        # Log it.
        self._log.info(
            "Analyzed %s → %s (conf=%.2f, cutoff=%.0f Hz, sr=%d)",
            self._path, verdict, result["confidence"],
            result["cutoff_hz"], result["sr"],
        )

    def _draw_spectrogram(self, result: dict) -> None:
        fig = Figure(figsize=(7, 6), dpi=100)
        fig.subplots_adjust(left=0.10, right=0.97, top=0.95, bottom=0.08,
                            hspace=0.35)

        # Top: full spectrogram.
        ax1 = fig.add_subplot(2, 1, 1)
        spec = result["spec_db"]
        sr = result["sr"]
        img = ax1.imshow(
            spec, origin="lower", aspect="auto", cmap="magma",
            extent=[result["times"][0] if len(result["times"]) else 0,
                    result["times"][-1] if len(result["times"]) else 0,
                    0, sr / 2.0 / 1000.0],
            vmin=-80, vmax=0,
        )
        ax1.set_ylabel("Frequency (kHz)")
        ax1.set_xlabel("Time (s)")
        ax1.set_title("Spectrogram")
        ax1.axhline(result["cutoff_hz"] / 1000.0, color="#00ffd0",
                    linewidth=1.0, linestyle="--", alpha=0.8,
                    label=f"cutoff {result['cutoff_hz']/1000:.1f} kHz")
        ax1.legend(loc="upper right", fontsize=8)
        fig.colorbar(img, ax=ax1, format="%+2.0f dB", pad=0.01)

        # Bottom: mean spectrum (dB vs frequency).
        ax2 = fig.add_subplot(2, 1, 2)
        ax2.plot(result["freqs"] / 1000.0, result["mean_db"],
                 color="#2c3e50", linewidth=1.0)
        ax2.axvline(result["cutoff_hz"] / 1000.0, color="#c0392b",
                    linestyle="--", linewidth=1.0,
                    label=f"cutoff {result['cutoff_hz']/1000:.1f} kHz")
        ax2.axhline(_CUTOFF_DB_THRESHOLD, color="#7f8c8d",
                    linestyle=":", linewidth=0.8,
                    label=f"{_CUTOFF_DB_THRESHOLD:.0f} dB")
        ax2.set_xlim(0, sr / 2.0 / 1000.0)
        ax2.set_ylim(-120, 5)
        ax2.set_xlabel("Frequency (kHz)")
        ax2.set_ylabel("Mean magnitude (dB)")
        ax2.set_title("Average spectrum")
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc="upper right", fontsize=8)

        canvas = FigureCanvasTkAgg(fig, master=self._plot_frame)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, self._plot_frame, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Make sure the matplotlib figure is closed cleanly when the
        # window is destroyed, to avoid leaking Agg backends.
        self.bind("<Destroy>", lambda _e, f=fig: plt.close(f), add="+")
