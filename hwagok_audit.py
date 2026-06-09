"""
EE-side Hwagok audit.

Question: is Hwagok's AlphaEarth drift similar to Mangwon/Mullae (the
active_panel cases the axis was supposed to fit), or is it a different
physical-change mode that contaminated the projection?

This script does not consult MOLIT, permits, or maps. It answers a
narrow embedding-space question only. Label classification of the
underlying mechanism (commercial gentrification vs redevelopment vs
apartment renewal vs ordinary built-form change) requires MOLIT and
is out of scope here.

Outputs:
  - outputs/hwagok_audit.png       (4-panel diagnostic plot)
  - console summary with the interpretation rule applied
"""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).parent
EMBED_PATH = ROOT / "data" / "alphaearth_ee.parquet"
OUT_PATH = ROOT / "outputs" / "hwagok_audit.png"

BANDS = [f"A{i:02d}" for i in range(64)]

COMPARISON_SET = [
    ("Hwagok",    11500580, "suspect"),
    ("Myeonmok",  11260545, "second-high control"),
    ("Mangwon",   11440730, "strong active_panel"),
    ("Mullae",    11560530, "weak active_panel"),
    ("Apgujeong", 11680105, "affluent stable"),
    ("Daechi",    11680117, "affluent stable"),
]

ROLE_COLORS = {
    "suspect":              "#d62728",
    "second-high control":  "#ff7f0e",
    "strong active_panel":  "#1f77b4",
    "weak active_panel":    "#17becf",
    "affluent stable":      "#7f7f7f",
}


def load_embeddings() -> pd.DataFrame:
    df = pd.read_parquet(EMBED_PATH).sort_values(["dong_code", "year"]).reset_index(drop=True)
    needed = {code for _, code, _ in COMPARISON_SET}
    missing = needed - set(df["dong_code"].unique())
    if missing:
        raise RuntimeError(f"Missing dong_codes in parquet: {missing}")
    return df[df["dong_code"].isin(needed)].copy()


def yoy_magnitudes(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-dong, per-year-pair L2 norm of embedding delta."""
    rows = []
    for code, g in panel.groupby("dong_code"):
        g = g.sort_values("year").reset_index(drop=True)
        years = g["year"].to_numpy()
        emb = g[BANDS].to_numpy(dtype=np.float64)
        for i in range(1, len(g)):
            delta = emb[i] - emb[i - 1]
            rows.append({
                "dong_code": int(code),
                "year_to":   int(years[i]),
                "yoy_norm":  float(np.linalg.norm(delta)),
            })
    return pd.DataFrame(rows)


def cumulative_drift_vectors(panel: pd.DataFrame, start: int = 2017, end: int = 2024):
    """Return dict code -> 64-vector (embedding[end] - embedding[start])."""
    vecs = {}
    for code, g in panel.groupby("dong_code"):
        g = g.set_index("year")
        if start not in g.index or end not in g.index:
            continue
        v = g.loc[end, BANDS].to_numpy(dtype=np.float64) - g.loc[start, BANDS].to_numpy(dtype=np.float64)
        vecs[int(code)] = v
    return vecs


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return float("nan")
    return float(np.dot(a, b) / (na * nb))


def cosine_matrix(vecs: dict, order: list[int]) -> np.ndarray:
    n = len(order)
    M = np.full((n, n), np.nan)
    for i, ci in enumerate(order):
        for j, cj in enumerate(order):
            M[i, j] = cosine(vecs[ci], vecs[cj])
    return M


def summarise(yoy: pd.DataFrame, cos: np.ndarray, names: list[str], code_by_name: dict) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("EE-SIDE HWAGOK AUDIT — AlphaEarth-only, 2017–2024")
    lines.append("=" * 72)
    lines.append("")
    lines.append("Source : data/alphaearth_ee.parquet (64-band annual embeddings)")
    lines.append("Scope  : whether Hwagok's drift matches Mangwon/Mullae or is a")
    lines.append("         distinct physical-change mode. Mechanism naming is NOT")
    lines.append("         decidable from this evidence alone.")
    lines.append("")
    lines.append("[1] Year-on-year embedding-change magnitude (L2 norm of Δ)")
    lines.append("-" * 72)
    summary = (
        yoy.assign(name=yoy["dong_code"].map({v: k for k, v in code_by_name.items()}))
           .groupby("name")["yoy_norm"]
           .agg(["mean", "max", "std"])
           .reindex(names)
    )
    lines.append(summary.to_string(float_format=lambda x: f"{x:7.4f}"))
    lines.append("")
    peak = (
        yoy.assign(name=yoy["dong_code"].map({v: k for k, v in code_by_name.items()}))
           .sort_values("yoy_norm", ascending=False)
           .groupby("name", as_index=False)
           .head(1)
           .set_index("name")[["year_to", "yoy_norm"]]
           .reindex(names)
    )
    lines.append("Peak YoY year per dong:")
    lines.append(peak.to_string(float_format=lambda x: f"{x:7.4f}"))
    lines.append("")

    lines.append("[2] Cosine similarity of cumulative drift vectors (Δ = 2024 − 2017)")
    lines.append("-" * 72)
    header = " " * 11 + "".join(f"{n[:9]:>10s}" for n in names)
    lines.append(header)
    for i, n in enumerate(names):
        row = f"{n[:9]:>10s} " + "".join(f"{cos[i, j]:>10.3f}" for j in range(len(names)))
        lines.append(row)
    lines.append("")

    h = names.index("Hwagok")
    c_mang  = cos[h, names.index("Mangwon")]
    c_mul   = cos[h, names.index("Mullae")]
    c_myeon = cos[h, names.index("Myeonmok")]
    c_apg   = cos[h, names.index("Apgujeong")]
    c_dae   = cos[h, names.index("Daechi")]

    lines.append("[3] Hwagok pairwise readout")
    lines.append("-" * 72)
    lines.append(f"  cos(Hwagok, Mangwon)   = {c_mang:+.3f}")
    lines.append(f"  cos(Hwagok, Mullae)    = {c_mul:+.3f}")
    lines.append(f"  cos(Hwagok, Myeonmok)  = {c_myeon:+.3f}")
    lines.append(f"  cos(Hwagok, Apgujeong) = {c_apg:+.3f}")
    lines.append(f"  cos(Hwagok, Daechi)    = {c_dae:+.3f}")
    lines.append("")

    # All-pairs view: who else is the global outlier?
    n = len(names)
    off_diag_means = {}
    for i, ni in enumerate(names):
        vals = [cos[i, j] for j in range(n) if j != i]
        off_diag_means[ni] = float(np.mean(vals))
    lines.append("  Average cosine to the other five dongs (lower = more outlier):")
    for ni in names:
        lines.append(f"    {ni:<10s}  mean cos = {off_diag_means[ni]:+.3f}")
    lines.append("")

    hwagok_yoy = yoy[yoy["dong_code"] == code_by_name["Hwagok"]]
    peak_year = int(hwagok_yoy.loc[hwagok_yoy["yoy_norm"].idxmax(), "year_to"])
    peak_mag  = float(hwagok_yoy["yoy_norm"].max())
    mean_mag  = float(hwagok_yoy["yoy_norm"].mean())
    spikiness = peak_mag / mean_mag if mean_mag > 0 else float("nan")

    # Is there a universal peak year? If yes that itself is a finding.
    peak_years = (
        yoy.sort_values("yoy_norm", ascending=False)
           .groupby("dong_code", as_index=False)
           .head(1)["year_to"]
           .tolist()
    )
    universal_peak = (len(set(peak_years)) == 1)
    common_peak = peak_years[0] if universal_peak else None

    lines.append("[4] Interpretation rule applied")
    lines.append("-" * 72)
    active_pair_mean = (c_mang + c_mul) / 2
    affluent_mean    = (c_apg + c_dae) / 2

    verdicts = []
    # Diagnostic 1 — is Hwagok the outlier the original question expected?
    hwagok_rank = sorted(off_diag_means.values()).index(off_diag_means["Hwagok"]) + 1
    most_outlier = min(off_diag_means, key=off_diag_means.get)
    verdicts.append(
        f"  Outlier ranking by mean cosine: {most_outlier} is the LEAST aligned "
        f"with the rest (mean cos = {off_diag_means[most_outlier]:+.3f}). "
        f"Hwagok ranks #{hwagok_rank} of 6 (mean cos = {off_diag_means['Hwagok']:+.3f})."
    )
    if most_outlier != "Hwagok":
        verdicts.append(
            f"  -> The premise 'Hwagok is the rogue control' is NOT supported "
            f"by the embedding evidence. Hwagok's drift is well-aligned with "
            f"Myeonmok ({c_myeon:+.2f}), Daechi ({c_dae:+.2f}), Mangwon "
            f"({c_mang:+.2f}), and Apgujeong ({c_apg:+.2f}). The real outlier "
            f"is {most_outlier}."
        )

    # Diagnostic 2 — axis cleanliness
    if c_apg >= 0.7 and c_dae >= 0.7 and c_mang >= 0.7:
        verdicts.append(
            f"  Hwagok's drift direction is simultaneously aligned with the "
            f"affluent stable controls (Apgujeong {c_apg:+.2f}, Daechi {c_dae:+.2f}) "
            f"AND with the strong active_panel case Mangwon ({c_mang:+.2f}). "
            f"That is structurally bad news for any projection axis learned from "
            f"these embeddings: it suggests the dominant drift direction is "
            f"shared across very different neighborhood types, so the axis is "
            f"not gentrification-specific. The 'high Hwagok score' is consistent "
            f"with the axis picking up a Seoul-wide common-mode trend."
        )

    # Diagnostic 3 — episodicity
    if spikiness >= 1.8:
        verdicts.append(
            f"  Hwagok YoY profile is spiky: peak/mean = {spikiness:.2f} in "
            f"{peak_year}. Consistent with a discrete shock rather than gradual "
            f"change. Mechanism naming (redevelopment vs gentrification vs "
            f"apartment renewal) requires MOLIT + permits."
        )
    else:
        verdicts.append(
            f"  Hwagok YoY profile is not strongly episodic: peak/mean = "
            f"{spikiness:.2f}. Consistent with cumulative drift rather than a "
            f"one-off shock."
        )

    # Diagnostic 4 — universal-peak artifact?
    if universal_peak:
        verdicts.append(
            f"  WARNING: ALL six dongs (suspect, active_panel, weak active_panel, "
            f"and both affluent stable controls) peak in YoY change in the SAME "
            f"year ({common_peak}). This is unlikely to be a substantive "
            f"neighborhood-level coincidence. Plausible causes: AlphaEarth "
            f"V1 annual-mosaic processing change, Sentinel-2 input gap, or a "
            f"genuine Seoul-wide built-environment event in {common_peak}. "
            f"This must be investigated before treating YoY magnitudes from "
            f"this dataset as a displacement signal at all."
        )

    for v in verdicts:
        lines.append(v)
    lines.append("")
    lines.append("[5] Limits — what this audit CANNOT decide")
    lines.append("-" * 72)
    lines.append("  • Whether Hwagok's change is commercial gentrification, ")
    lines.append("    redevelopment, apartment renewal, or routine built-form ")
    lines.append("    turnover. AlphaEarth measures physical change, not ")
    lines.append("    displacement. Resolve via MOLIT rent/sale audit + permits.")
    lines.append("  • Whether the 'stable mid-income residential' label in ")
    lines.append("    labeled_cases.csv is wrong. The audit only tells us if ")
    lines.append("    Hwagok looks more like Mangwon/Mullae than like Apgujeong/")
    lines.append("    Daechi in embedding space.")
    lines.append("=" * 72)
    return "\n".join(lines)


def plot_diagnostics(yoy: pd.DataFrame, cos: np.ndarray, vecs: dict,
                     names: list[str], code_by_name: dict, roles: dict) -> None:
    name_by_code = {v: k for k, v in code_by_name.items()}
    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.28)

    # Panel A: YoY magnitude time series
    ax = fig.add_subplot(gs[0, 0])
    for name in names:
        code = code_by_name[name]
        sub = yoy[yoy["dong_code"] == code].sort_values("year_to")
        lw = 2.6 if name == "Hwagok" else 1.6
        ls = "-" if name == "Hwagok" else ("--" if "affluent" in roles[name] else "-")
        ax.plot(sub["year_to"], sub["yoy_norm"],
                marker="o", lw=lw, ls=ls,
                color=ROLE_COLORS[roles[name]], label=f"{name} ({roles[name]})")
    ax.set_title("A. Year-on-year embedding-change magnitude", fontsize=11, weight="bold")
    ax.set_xlabel("Year (Δ from previous year)")
    ax.set_ylabel("‖embedding(y) − embedding(y−1)‖₂")
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.3)

    # Panel B: cumulative drift magnitude (bar)
    ax = fig.add_subplot(gs[0, 1])
    mags = [np.linalg.norm(vecs[code_by_name[n]]) for n in names]
    colors = [ROLE_COLORS[roles[n]] for n in names]
    bars = ax.bar(names, mags, color=colors, edgecolor="black", linewidth=0.6)
    for b, m in zip(bars, mags):
        ax.text(b.get_x() + b.get_width() / 2, m, f"{m:.3f}",
                ha="center", va="bottom", fontsize=9)
    ax.set_title("B. Cumulative drift magnitude ‖Δ 2024−2017‖₂", fontsize=11, weight="bold")
    ax.set_ylabel("L2 norm")
    ax.tick_params(axis="x", rotation=20)
    ax.grid(axis="y", alpha=0.3)

    # Panel C: band-level cumulative drift profile
    ax = fig.add_subplot(gs[1, 0])
    band_idx = np.arange(64)
    for name in names:
        v = vecs[code_by_name[name]]
        lw = 2.4 if name == "Hwagok" else 1.0
        alpha = 1.0 if name == "Hwagok" else 0.6
        ax.plot(band_idx, v, lw=lw, alpha=alpha,
                color=ROLE_COLORS[roles[name]], label=name)
    ax.axhline(0, color="black", lw=0.6, ls=":")
    ax.set_title("C. Band-level cumulative drift (Δ 2024−2017)", fontsize=11, weight="bold")
    ax.set_xlabel("AlphaEarth band index (A00–A63)")
    ax.set_ylabel("Drift")
    ax.legend(fontsize=8, ncol=2, loc="best")
    ax.grid(alpha=0.3)

    # Panel D: cosine similarity heatmap
    ax = fig.add_subplot(gs[1, 1])
    im = ax.imshow(cos, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(names)))
    ax.set_yticks(range(len(names)))
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=9)
    ax.set_yticklabels(names, fontsize=9)
    for i in range(len(names)):
        for j in range(len(names)):
            ax.text(j, i, f"{cos[i, j]:+.2f}",
                    ha="center", va="center",
                    color="white" if abs(cos[i, j]) > 0.55 else "black",
                    fontsize=8)
    ax.set_title("D. Cosine similarity of drift vectors", fontsize=11, weight="bold")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle("Hwagok AlphaEarth audit (2017–2024)  —  diagnostic only, not validation",
                 fontsize=13, weight="bold", y=0.995)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=140, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    panel = load_embeddings()
    names = [n for n, _, _ in COMPARISON_SET]
    code_by_name = {n: c for n, c, _ in COMPARISON_SET}
    roles = {n: r for n, _, r in COMPARISON_SET}

    yoy = yoy_magnitudes(panel)
    vecs = cumulative_drift_vectors(panel, start=2017, end=2024)
    order = [code_by_name[n] for n in names]
    cos = cosine_matrix(vecs, order)

    print(summarise(yoy, cos, names, code_by_name))
    plot_diagnostics(yoy, cos, vecs, names, code_by_name, roles)
    print(f"\nSaved diagnostic plot → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
