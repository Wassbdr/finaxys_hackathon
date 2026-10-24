"""Files the Java engine reads: a binary event log and a properties config."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES
from sentinel.detect.rules import Rule
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.types import CANCEL, EXEC, MODIFY, NEW

EVENT_MAGIC = b"SNTLEV01"
N_FIELDS = 15  # ts, instrument idx, event type, order id, trader id, counterparty id, side, price, qty, 6 snapshot fields
_EVENT_CODES = {"NEW": NEW, "CANCEL": CANCEL, "MODIFY": MODIFY, "EXEC": EXEC}
_SNAPSHOT = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "bid_depth5", "ask_depth5")


def events_to_array(events: pd.DataFrame, instruments: Sequence[str]) -> np.ndarray:
    """Event log as an (n, 15) little-endian int64 array in the file's field order."""
    index = {name: i for i, name in enumerate(instruments)}
    arr = np.empty((len(events), N_FIELDS), dtype="<i8")
    arr[:, 0] = events["ts"].to_numpy()
    arr[:, 1] = events["instrument"].astype(str).map(index).to_numpy()
    arr[:, 2] = events["event_type"].astype(str).map(_EVENT_CODES).to_numpy()
    for column, name in enumerate(("order_id", "trader_id", "counterparty_id", "side", "price", "qty", *_SNAPSHOT), 3):
        arr[:, column] = events[name].to_numpy()
    return arr


def write_events(events: pd.DataFrame, path: Path) -> list[str]:
    """Write events.bin (magic, int64 count, records). Returns the instrument names in index order."""
    instruments = sorted(events["instrument"].astype(str).unique())
    arr = events_to_array(events, instruments)
    with Path(path).open("wb") as f:
        f.write(EVENT_MAGIC)
        f.write(struct.pack("<q", len(arr)))
        arr.tofile(f)
    return instruments


def read_events(path: Path) -> np.ndarray:
    """Read events.bin back as an (n, 15) int64 array (used by tests)."""
    raw = Path(path).read_bytes()
    if raw[:8] != EVENT_MAGIC:
        raise ValueError("not an events.bin file")
    (count,) = struct.unpack("<q", raw[8:16])
    return np.frombuffer(raw, dtype="<i8", offset=16, count=count * N_FIELDS).reshape(count, N_FIELDS)


def write_engine_config(path: Path, *, window_s: int, instruments: Sequence[str],
                        thresholds: Mapping[str, float], rule_sets: Mapping[str, Sequence[Rule]]) -> None:
    """Write engine.properties: window length, feature order, model thresholds and every rule.

    Floats use ``repr`` so the Java side parses back the identical double.
    """
    lines = [f"window_s={window_s}", f"instruments={','.join(instruments)}", f"features={','.join(FEATURE_NAMES)}"]
    lines += [f"threshold.{kind}={float(thresholds[kind])!r}" for kind in CLASSES[1:]]
    rules = [(detector, rule) for detector, group in rule_sets.items() for rule in group]
    lines.append(f"rule.count={len(rules)}")
    for i, (detector, rule) in enumerate(rules):
        conditions = ";".join(f"{c.feature}{c.op}{float(c.threshold)!r}" for c in rule.conditions)
        lines += [f"rule.{i}.detector={detector}", f"rule.{i}.kind={rule.kind}",
                  f"rule.{i}.mode={rule.mode}", f"rule.{i}.conditions={conditions}"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
