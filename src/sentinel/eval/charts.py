"""Figures for RESULTS.md. Fixed color per detector; thin marks; legend always; no dual axes."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
COLORS = {"LightGBM": "#2a78d6", "tuned rules": "#eb6834", "textbook rules": "#1baf7a",
          "generic rule": "#eda100", "Isolation Forest": "#e87ba4"}
ENGINE_COLORS = {"Python": "#4a3aa7", "Java": "#008300"}
ORDER = ("LightGBM", "tuned rules", "textbook rules", "generic rule", "Isolation Forest")
KINDS = ("SPOOFING", "LAYERING", "WASH")
LEVELS = ("easy", "medium", "hard")


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=9, length=0)


def _fig(ncols: int, width: float, height: float):
    fig, axes = plt.subplots(1, ncols, figsize=(width, height), dpi=160, facecolor=SURFACE)
    return fig, np.atleast_1d(axes)


def pr_figure(pr: Mapping, path: Path) -> None:
    """Precision-recall curves per type. Curves: LightGBM, Isolation Forest. Points: rules. Ring: validation threshold."""
    fig, axes = _fig(3, 13, 4.6)
    for ax, kind in zip(axes, KINDS):
        _style(ax)
        d = pr[kind]
        for det in ("Isolation Forest", "LightGBM"):
            c = d["curves"][det]
            ax.plot(c["recall"], c["precision"], color=COLORS[det], linewidth=2, solid_capstyle="round",
                    solid_joinstyle="round")
        for det, (p, r) in d["points"].items():
            ax.plot([r], [p], marker="o", markersize=9, markerfacecolor=COLORS[det], markeredgecolor=SURFACE,
                    markeredgewidth=2, linestyle="none")
        for det, (p, r) in d["operating"].items():
            ax.plot([r], [p], marker="o", markersize=10, markerfacecolor=COLORS[det], markeredgecolor=SURFACE,
                    markeredgewidth=2, linestyle="none", zorder=5)
        p, r = d["operating"]["LightGBM"]
        ax.annotate("LightGBM at the\nvalidation threshold", xy=(r, p), xytext=(12, 12), textcoords="offset points",
                    fontsize=8, color=INK2)
        ax.set_xlim(-0.03, 1.03)
        ax.set_ylim(-0.04, 1.03)
        ax.set_xlabel("Recall", color=INK2, fontsize=9)
        if ax is axes[0]:
            ax.set_ylabel("Precision", color=INK2, fontsize=9)
        ap = d["ap"]
        ax.set_title(f"{kind.title()}", loc="left", color=INK, fontsize=11, pad=22)
        ax.text(0, 1.03, f"average precision: LightGBM {ap['LightGBM']:.2f}, Isolation Forest {ap['Isolation Forest']:.2f}",
                transform=ax.transAxes, color=INK2, fontsize=8)
    handles = [Line2D([0], [0], color=COLORS[d], linewidth=2, label=f"{d} (curve)") for d in ("LightGBM", "Isolation Forest")]
    handles += [Line2D([0], [0], marker="o", linestyle="none", markersize=9, markerfacecolor=COLORS[d],
                       markeredgecolor=SURFACE, markeredgewidth=2, label=f"{d} (single point)")
                for d in ("tuned rules", "textbook rules", "generic rule")]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, labelcolor=INK2, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def recall_figure(recall: Mapping, path: Path) -> None:
    """Window recall by difficulty for every detector, with 95% cluster-bootstrap intervals."""
    fig, axes = _fig(3, 13, 4.4)
    x = np.arange(len(LEVELS))
    width = 0.11
    offsets = (np.arange(len(ORDER)) - (len(ORDER) - 1) / 2) * (width + 0.02)
    for ax, kind in zip(axes, KINDS):
        _style(ax)
        for j, det in enumerate(ORDER):
            cells = [recall[kind][det][lvl] for lvl in LEVELS]
            vals = np.array([c["point"] for c in cells])
            lo = np.array([c["lo"] for c in cells])
            hi = np.array([c["hi"] for c in cells])
            ax.bar(x + offsets[j], vals, width, color=COLORS[det])
            ax.errorbar(x + offsets[j], vals, yerr=[vals - lo, hi - vals], fmt="none", ecolor=INK2,
                        elinewidth=1.1, capsize=2)
            if det == "LightGBM":
                # sit above the neighbouring tuned-rules interval too, so the label never touches its bar
                nxt = np.array([recall[kind]["tuned rules"][lvl]["hi"] for lvl in LEVELS])
                for xi, v, h in zip(x + offsets[j], vals, np.maximum(hi, nxt)):
                    ax.text(xi, min(h + 0.03, 1.04), f"{v:.2f}", ha="center", fontsize=8, color=INK)
        ax.set_xticks(x, [lvl.title() for lvl in LEVELS])
        ax.set_ylim(0, 1.1)
        ax.set_title(kind.title(), loc="left", color=INK, fontsize=11)
        if ax is axes[0]:
            ax.set_ylabel("Recall (windows)", color=INK2, fontsize=9)
    fig.legend(handles=[Patch(facecolor=COLORS[d], label=d) for d in ORDER], loc="lower center", ncol=5,
               frameon=False, labelcolor=INK2, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def _human_ns(value: float, upper_bound: bool = False) -> str:
    text = f"{value / 1000:.1f} µs" if value >= 1000 else f"{value:.0f} ns"
    return ("≤" + text) if upper_bound else text


def performance_figure(bench: Mapping, path: Path) -> None:
    """Throughput and per-event latency of the Python reference and the Java engine."""
    fig, axes = _fig(2, 11, 4.4)
    ax1, ax2 = axes
    engines = (("Python", bench["python"]), ("Java", bench["java_v2"]))
    _style(ax1)
    for i, (name, run) in enumerate(engines):
        v = run["throughput_eps"] / 1e6
        ax1.bar(i, v, 0.4, color=ENGINE_COLORS[name])
        ax1.text(i, v + 0.04, f"{v:.2f}M/s", ha="center", va="bottom", fontsize=9, color=INK)
    ax1.set_xticks([0, 1], [e[0] for e in engines])
    ax1.set_ylabel("Events per second (millions)", color=INK2, fontsize=9)
    ax1.set_title("Throughput (single thread)", loc="left", color=INK, fontsize=11)
    ax1.set_ylim(0, max(e[1]["throughput_eps"] for e in engines) / 1e6 * 1.15)

    _style(ax2)
    metrics = (("median", "Median"), ("p99", "p99"), ("p999", "p99.9"))
    x = np.arange(len(metrics))
    width = 0.32
    for i, (name, run) in enumerate(engines):
        vals = [run["latency_ns"]["overall"][m] for m, _ in metrics]
        ax2.bar(x + (i - 0.5) * (width + 0.04), vals, width, color=ENGINE_COLORS[name])
        for xi, (m, _), v in zip(x + (i - 0.5) * (width + 0.04), metrics, vals):
            ax2.text(xi, v * 1.12, _human_ns(v, upper_bound=(name == "Java" and m == "median")),
                     ha="center", fontsize=8, color=INK)
    ax2.set_yscale("log")
    ax2.set_xticks(x, [label for _, label in metrics])
    ax2.set_ylabel("Latency per event (ns, log scale)", color=INK2, fontsize=9)
    ax2.set_title("Latency per event", loc="left", color=INK, fontsize=11)
    ax2.set_ylim(bottom=30, top=max(e[1]["latency_ns"]["overall"]["p999"] for e in engines) * 6)
    fig.legend(handles=[Patch(facecolor=ENGINE_COLORS[n], label=n) for n, _ in engines], loc="lower center", ncol=2,
               frameon=False, labelcolor=INK2, fontsize=9)
    fig.text(0.99, 0.01, "Java median is at the 100 ns timer resolution (Windows).", ha="right", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
