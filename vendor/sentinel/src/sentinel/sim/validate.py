"""Compare simulator statistics with a real LOBSTER sample.

Assumed LOBSTER format (verify against the docs on the download page; the
loader is the only code that depends on it):
* message file, no header: time (seconds after midnight), type
  (1 new, 2 partial cancel, 3 delete, 4 visible execution, 5 hidden execution),
  order id, size, price (dollars x 10000), direction (-1 sell, 1 buy).
* orderbook file, no header, per level: ask price, ask size, bid price, bid size.
"""
from __future__ import annotations

from typing import IO

import numpy as np
import pandas as pd

_MISSING_PRICE = 9_999_999_999  # LOBSTER's sentinel for an empty book side
# Gaps below 1 microsecond are burst artifacts, not separate arrivals: the simulator stamps the
# several events of one agent action 1 ns apart. The filter is applied to both sides.
_MIN_GAP_MS = 1e-3


def load_lobster(message_csv: str | IO[str], book_csv: str | IO[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load a LOBSTER message file and orderbook file."""
    message = pd.read_csv(message_csv, header=None, names=["time", "type", "order_id", "size", "price", "direction"])
    book = pd.read_csv(book_csv, header=None)
    return message, book


def _quantiles(values: np.ndarray, prefix: str) -> dict[str, float]:
    p50, p90, p99 = np.quantile(values, [0.5, 0.9, 0.99])
    return {f"{prefix}_p50": float(p50), f"{prefix}_p90_over_p50": float(p90 / p50),
            f"{prefix}_p99_over_p50": float(p99 / p50)}


def lobster_stats(message: pd.DataFrame, book: pd.DataFrame, price_tick: int = 100) -> dict[str, float]:
    """Scale-free summary statistics of a LOBSTER day (price_tick = one cent in LOBSTER units)."""
    new = message[message["type"] == 1]
    cancels = message[message["type"].isin([2, 3])]
    quotes = message[message["type"].isin([1, 2, 3])]
    inter_event_ms = np.diff(quotes["time"].to_numpy()) * 1_000
    inter_event_ms = inter_event_ms[inter_event_ms >= _MIN_GAP_MS]

    # Book snapshot statistics use the same rows as the simulator side: submissions and cancels.
    rows = message["type"].isin([1, 2, 3]).to_numpy()
    book = book[rows]
    ask1, bid1 = book[0].to_numpy(), book[2].to_numpy()
    valid = (ask1 < _MISSING_PRICE) & (bid1 > -_MISSING_PRICE) & (bid1 > 0)
    spread_ticks = (ask1[valid] - bid1[valid]) / price_tick
    n_levels = min(book.shape[1] // 4, 5)
    ask_sizes = sum(book[4 * k + 1].to_numpy() for k in range(n_levels))
    bid_sizes = sum(book[4 * k + 3].to_numpy() for k in range(n_levels))
    size_median = float(new["size"].median())

    stats = {
        "cancel_to_new": len(cancels) / max(1, len(new)),
        "spread_p10_ticks": float(np.quantile(spread_ticks, 0.1)),
        "spread_p50_ticks": float(np.quantile(spread_ticks, 0.5)),
        "spread_p90_ticks": float(np.quantile(spread_ticks, 0.9)),
        "depth1_over_median_size": float(np.median((book[1].to_numpy() + book[3].to_numpy()) / 2) / size_median),
        "depth5_over_median_size": float(np.median((ask_sizes + bid_sizes) / 2) / size_median),
    }
    stats.update(_quantiles(new["size"].to_numpy(dtype=float), "size"))
    stats.update(_quantiles(inter_event_ms, "iet_ms") if len(inter_event_ms) else {})
    stats["iet_p50_ms"] = stats.pop("iet_ms_p50", float("nan"))
    return stats


def sim_stats(events: pd.DataFrame) -> dict[str, float]:
    """The same statistics from the simulator's event log, matched to LOBSTER's definitions.

    * "new" is limit submissions only (LOBSTER type 1 has no market orders).
    * cancels include MODIFY rows, which are size reductions like LOBSTER's partial cancels.
    * clocks and snapshots use submissions, cancels and modifies only, so each event counts
      once (a fill logs two EXEC rows that share one snapshot).
    """
    etype = events["event_type"].astype(str)
    limit_new = (etype == "NEW") & (events["price"] > 0)
    new = events[limit_new]
    n_cancel = int(etype.isin(["CANCEL", "MODIFY"]).sum())
    quoting = events[limit_new | etype.isin(["CANCEL", "MODIFY"])]
    gaps = quoting.groupby("instrument", observed=True)["ts"].diff().dropna().to_numpy() / 1e6
    gaps = gaps[gaps >= _MIN_GAP_MS]

    snaps = quoting
    quoted = (snaps["best_bid"] > 0) & (snaps["best_ask"] > 0)
    spread = (snaps["best_ask"] - snaps["best_bid"])[quoted].to_numpy()
    size_median = float(new["qty"].median())

    stats = {
        "cancel_to_new": n_cancel / max(1, len(new)),
        "spread_p10_ticks": float(np.quantile(spread, 0.1)),
        "spread_p50_ticks": float(np.quantile(spread, 0.5)),
        "spread_p90_ticks": float(np.quantile(spread, 0.9)),
        "depth1_over_median_size": float(((snaps["bid_qty1"] + snaps["ask_qty1"]) / 2).median() / size_median),
        "depth5_over_median_size": float(((snaps["bid_depth5"] + snaps["ask_depth5"]) / 2).median() / size_median),
    }
    stats.update(_quantiles(new["qty"].to_numpy(dtype=float), "size"))
    stats.update(_quantiles(gaps, "iet_ms"))
    stats["iet_p50_ms"] = stats.pop("iet_ms_p50")
    return stats


def comparison_markdown(sim: dict[str, float], real: dict[str, float]) -> str:
    """Side-by-side markdown table of the shared statistics."""
    lines = ["| statistic | simulator | LOBSTER sample |", "|---|---|---|"]
    for key in sim:
        if key in real:
            lines.append(f"| {key} | {sim[key]:.3f} | {real[key]:.3f} |")
    return "\n".join(lines) + "\n"
