"""Deux détecteurs lisibles, calibrés sur une référence vérifiée sans contamination."""

from collections import defaultdict
from statistics import median


def robust_z(value, med, mad, floor):
    return 0.6745 * (value - med) / max(mad, 0.1 * abs(med), floor)


def cusum(previous, z):
    return max(0.0, previous + min(z, 8.0) - 0.5)


def features(orders, tick):
    recent = [m for m in orders if tick - 9 <= m[0] <= tick]
    return {
        "count": len(recent),
        "mean": sum(m[1] for m in recent) / len(recent) if recent else 0.0,
    }


def structuring(orders, tick, limit, profile):
    window = [m for m in orders if tick - 20 <= m[0] <= tick]
    total = sum(m[1] for m in window)
    near = sum(0.9 * limit <= m[1] < limit for m in window)
    count_threshold = profile.get("near_threshold", 3)
    return {
        "alert": (near >= count_threshold or total >= 2 * limit)
        and total > profile.get("max_cumulative_20", 2 * limit),
        "near_count": near,
        "cumulative_eur": total,
        "limit_eur": limit,
        "window_ticks": 20,
        "near_threshold": count_threshold,
        "reference_max_eur": profile.get("max_cumulative_20", 2 * limit),
    }


def calibrate(cfg, messages, ticks, keyring):
    if any(not keyring.verify(m) for m in messages):
        raise ValueError("REFERENCE_INVALID_SIGNATURE")
    if any(
        m["type"] == "service_status" and m["payload"].get("mode") != "normal"
        for m in messages
    ):
        raise ValueError("REFERENCE_CONTAMINATED")
    from sim.config import trader_ids

    records = defaultdict(list)
    samples = {a: {"count": [], "mean": []} for a in trader_ids(cfg)}
    maxima = {a: {"cumulative": 0.0, "near": 0} for a in samples}
    limit = cfg["agents"]["trader"]["max_order_eur"]
    for msg in messages:
        if msg["from"] == "clock" and msg["type"] == "tick":
            for a in samples:
                for name, value in features(records[a], msg["tick"]).items():
                    samples[a][name].append(value)
        elif msg["type"] == "order_proposal" and msg["from"] in samples:
            a = msg["from"]
            p = msg["payload"]
            records[a].append(
                (msg["tick"], float(p["notional_eur"]), p["asset"], msg["message_id"])
            )
            st = structuring(records[a], msg["tick"], limit, {})
            maxima[a]["cumulative"] = max(maxima[a]["cumulative"], st["cumulative_eur"])
            maxima[a]["near"] = max(maxima[a]["near"], st["near_count"])
    by_id = {m["message_id"]: m for m in messages}
    confidence_gaps = defaultdict(list)
    for m in messages:
        if m["type"] == "order_proposal":
            for id_ in m["depends_on"]:
                q = by_id.get(id_, {})
                estimate = (
                    q.get("payload", {})
                    .get("assets", {})
                    .get(m["payload"].get("asset"))
                )
                if q.get("from") == "quant" and estimate:
                    confidence_gaps[m["from"]].append(
                        abs(
                            float(m["payload"].get("quant_confidence", 0))
                            - float(estimate["confidence"])
                        )
                    )
    profiles = {}
    for agent, series in samples.items():
        stats = {}
        for name, values in series.items():
            med = median(values) if values else 0.0
            mad = median(abs(x - med) for x in values) if values else 0.0
            floor = 0.5 if name == "count" else 1.0
            prior = (
                0.2
                if name == "count"
                else cfg["agents"]["trader"]["max_order_eur"] * 0.2
            )
            effective = (
                (len(values) * med + 30 * prior) / (len(values) + 30)
                if len(values) < 50
                else med
            )
            score = maximum = 0.0
            for value in values:
                score = cusum(score, robust_z(value, effective, mad, floor))
                maximum = max(maximum, score)
            stats[name] = {
                "median": med,
                "effective_median": effective,
                "mad": mad,
                "floor": floor,
                "h": max(6.0, 1.5 * maximum),
                "reference_max_cusum": maximum,
            }
        orders = records[agent]
        profiles[agent] = {
            "clean": True,
            "confidence_gap_threshold": max(
                0.3,
                (
                    sorted(confidence_gaps[agent])[
                        int(0.99 * (len(confidence_gaps[agent]) - 1))
                    ]
                    if confidence_gaps[agent]
                    else 0.25
                )
                + 0.05,
            ),
            "reference_ticks": ticks,
            "reference_samples": len(series["count"]),
            "mean_order_eur": sum(m[1] for m in orders) / len(orders)
            if orders
            else 0.0,
            "frequency": len(orders) / ticks,
            "assets": sorted({m[2] for m in orders}),
            "max_cumulative_20": maxima[agent]["cumulative"],
            "max_near_20": maxima[agent]["near"],
            "near_threshold": maxima[agent]["near"] + 2
            if maxima[agent]["near"] >= 3
            else 3,
            "robust": stats,
        }
    return profiles


class Behavior:
    def __init__(self, profiles):
        self.profiles = profiles
        self.scores = defaultdict(dict)
        self.observations = {}
        self.evidence = defaultdict(set)

    def tick(self, agent, orders, tick):
        model = self.profiles.get(agent, {})
        if not model.get("clean"):
            return False
        values = features(orders, tick)
        alert = False
        state = {}
        for name, parameters in model.get("robust", {}).items():
            z = robust_z(
                values[name],
                parameters["effective_median"],
                parameters["mad"],
                parameters["floor"],
            )
            score = cusum(self.scores[agent].get(name, 0.0), z)
            self.scores[agent][name] = score
            state[name] = {
                "value": values[name],
                "z": z,
                "cusum": score,
                "threshold": parameters["h"],
            }
            alert |= score > parameters["h"]
        if any(v > 0 for v in self.scores[agent].values()):
            self.evidence.setdefault(agent, set()).update(
                o[3] for o in orders if tick - 9 <= o[0] <= tick
            )
        else:
            self.evidence[agent] = set()
        self.observations[agent] = state
        return alert
