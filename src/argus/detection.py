"""Independent event-based coordination signals plus Sentinel's per-agent detectors."""
from collections import defaultdict, deque
import json
from pathlib import Path
import networkx as nx
import numpy as np
from sentinel.features.extractor import WindowFeatureExtractor, FEATURE_NAMES, rows_to_frame
from sentinel.detect.alerts import rule_alerts, model_alerts
from sentinel.detect.rules import TEXTBOOK_RULES
from sentinel.detect.model import load_model
from sentinel.detect.explain import ModelExplainer
from dataclasses import asdict


class Coordination:
    horizon = 30_000_000_000
    tolerance = 80_000_000

    def __init__(self):
        self.recent = deque()
        self.matches = deque()
        self.trades = deque()

    def process(self, e):
        now = e["ts"]
        while self.recent and now - self.recent[0]["ts"] > self.tolerance:
            self.recent.popleft()
        while self.matches and now - self.matches[0]["ts"] > self.horizon:
            self.matches.popleft()
        while self.trades and now - self.trades[0]["ts"] > self.horizon:
            self.trades.popleft()
        if e["type"] == "EXEC" and e["side"] == 1:
            self.trades.append(e)  # Sentinel emits each fill twice; count the buy leg only.
        if e["type"] not in ("NEW", "CANCEL") or e["price"] <= 0:
            return
        for other in self.recent:
            if (other["type"] == e["type"] and other["agent"] != e["agent"]
                and other["side"] == e["side"] and abs(other["price"] - e["price"]) <= 2
                and min(other["qty"], e["qty"]) / max(other["qty"], e["qty"]) >= .8):
                self.matches.append(dict(ts=now, pair=tuple(sorted((other["agent"], e["agent"]))),
                                         kind=e["type"], ids=[other["id"], e["id"]]))
        self.recent.append(e)

    def graph(self):
        pairs = defaultdict(lambda: {"NEW": set(), "CANCEL": set(), "ids": set()})
        for m in self.matches:
            item = pairs[m["pair"]]
            item[m["kind"]].add(m["ts"] // 1_000_000_000)  # repeated bursts, not number of order pairs
            item["ids"].update(m["ids"])
        g = nx.Graph()
        edges = []
        for (a, b), p in pairs.items():
            n, c = len(p["NEW"]), len(p["CANCEL"])
            suspicious = n >= 3 and c >= 3
            if suspicious:
                g.add_edge(a, b)
            edges.append(dict(id=f"sync-{a}-{b}", source=a, target=b, kind="synchrony",
                              new_bursts=n, cancel_bursts=c, suspicious=suspicious,
                              event_ids=sorted(p["ids"])[-36:]))
        trades = defaultdict(lambda: {"volume": 0, "count": 0, "ids": []})
        for t in self.trades:
            k = tuple(sorted((t["agent"], t["counterparty"])))
            trades[k]["volume"] += t["qty"]
            trades[k]["count"] += 1
            trades[k]["ids"].append(t["id"])
        edges += [dict(id=f"trade-{a}-{b}", source=a, target=b, kind="trade", suspicious=False,
                       volume=v["volume"], count=v["count"], event_ids=v["ids"][-12:]) for (a,b),v in trades.items()]
        # A connected component alone is insufficient: all three pairs must corroborate a trio.
        groups = [sorted(c) for c in nx.enumerate_all_cliques(g) if len(c) == 3]
        return edges, groups


class Surveillance:
    def __init__(self, model_dir):
        self.extractor = WindowFeatureExtractor()
        self.booster = self.explainer = None
        self.thresholds = {}
        p = Path(model_dir)
        if (p / "lgbm.txt").exists():
            self.booster, self.thresholds = load_model(p)
            self.explainer = ModelExplainer(self.booster, json.loads((p / "reference.json").read_text()))

    def process(self, e):
        if e["type"] == "REJECT":
            return []
        rows = self.extractor.process(e["ts"], "SYN/USD", ("NEW","CANCEL","MODIFY","EXEC").index(e["type"]),
            e["order_id"], e["agent"], e["counterparty"], e["side"], e["price"], e["qty"],
            e["bid"], e["ask"], e["bid_qty"], e["ask_qty"], e["bid_depth"], e["ask_depth"])
        if not rows:
            return []
        df = rows_to_frame(rows)
        alerts = []
        for name, rule in TEXTBOOK_RULES.items():
            alerts.extend(rule_alerts(df, rule, name))
        if self.booster:
            proba = self.booster.predict(df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64), num_threads=1)
            alerts.extend(model_alerts(df, proba, self.thresholds, self.explainer))
        return [asdict(a) for a in alerts]
