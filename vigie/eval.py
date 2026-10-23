"""Bench for the pitch: every scenario with and without the Vigie, graded against the answer key.

One bench, not two: the replays and the grading are vigie/improve.py's; this adds the attack seeds,
writes the report (runs/vigie-improve/improvements.json, key "metrics") and vigie/METRIQUES.md.

    make metrics                                  # = python -m vigie.eval
    python -m vigie.eval --scenarios php2shell_day
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sim.config import ROOT
from vigie.improve import CLEAN, run

ATTACK_SEEDS = (42, 43, 44, 45, 46)


def num(x, digits=0):
    return "n.d." if x is None else f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def pct(x):
    return "n.d." if x is None else f"{num(100 * x)} %"


def meur(x):
    return "n.d." if x is None else f"{num(x / 1e6, 3)} M€"


def frac(a):
    return f"{pct(a['rate'])} ({a['succeeded']}/{a['attempted']})"


def delay(ticks, seconds, done, total):
    return f"n.d., {done}/{total}" if ticks is None else f"{ticks:+d} t ({num(seconds, 1)} s), {done}/{total}"


def wilson(w, unit):
    return f"{w['k']}/{w['n']} {unit} = {pct(w['rate'])} [{pct(w['low'])} ; {pct(w['high'])}]"


def mean(i, fmt=num):
    return f"{fmt(i['mean'])} [{fmt(i['low'])} ; {fmt(i['high'])}]"


def markdown(m, evidence):
    seeds = m["seeds"]
    lines = [
        "# Métriques de la Vigie",
        "",
        f"Généré par `make metrics` (`python -m vigie.eval`) le {datetime.now():%d/%m/%Y à %H:%M}. Preuves : `{evidence}`",
        "(journaux A et B, `comparison.json` par rejeu). Ce fichier se régénère : ne pas l’éditer à la main.",
        "",
        "## Protocole",
        "",
        "- Chaque scénario est rejoué en lockstep deux fois avec la même graine : A sans Vigie, B avec la Vigie",
        "  dans son propre processus Redis (`vigie/compare.py`).",
        f"- Calibrage : une journée propre de 300 ticks sur la graine {seeds['calibration']}, jamais mesurée.",
        f"  Mesure : graines d’attaque {', '.join(map(str, seeds['attack']))} ; graines propres {', '.join(map(str, seeds['clean']))} (`{CLEAN}`).",
        "- Vérité terrain, pour noter seulement (la Vigie ne la lit pas) : mode des services dans `states.jsonl`,",
        "  provenance des opérations, ordres injectés `corrupted:`, signatures invalides du journal.",
        f"- 1 tick = {num(m['tick_seconds'], 1)} s en démonstration.",
        "",
        "## Définitions",
        "",
        "- **ASR** (taux de succès d’attaque) : opérations contaminées exécutées / tentées (exécutées, refusées,",
        "  messages forgés rejetés). 0/0 : aucune tentative n’a atteint l’exécuteur.",
        "- **Précision, rappel** (opérations du monde B) : vrais refus / refus ; vrais refus / (vrais refus + contaminées passées).",
        "- **Détection** : premier signal de la Vigie (action, indicateur, constat de couche 1) qui nomme l’entité attaquée,",
        "  moins le tick de l’attaque. Négatif : vu avant la compromission, grâce à la CVE. Pire cas sur les entités",
        "  détectées, puis détectées / attaquées.",
        "- **Confinement** : premier acte contre l’entité (refus d’une opération qu’elle alimente, isolement, quarantaine)",
        "  après lequel aucune de ses opérations ne s’exécute. Pire cas, puis confinées / attaquées.",
        "- **Utilité** : opérations légitimes exécutées avec / sans Vigie.",
        "- **Rayon d’impact** : agents contaminés sans Vigie (vérité) → agents en quarantaine avec, sur 8 (exécuteur compris).",
        "- **Argent**, moyenne par rejeu : fraude bloquée = fraude A − fraude B ; résultat net sauvé = P&L net B − P&L net A ;",
        "  écart de trading = trading B − trading A (négatif : coût des blocages ; positif : pertes de trading évitées).",
        "  Les trois restent séparés : net sauvé = fraude bloquée + écart de trading.",
        "  Gelé : notionnel des opérations propres refusées, en attente d’un humain, pas perdu.",
        "- **Faux positifs** : sur les graines propres, tout refus, isolement ou alerte humaine est faux.",
        "  Proportions avec intervalle de Wilson à 95 % ; moyennes par rejeu avec bootstrap à 95 % sur les graines.",
        "- **Latence du portique** : temps mur de `BusGate.request_approval` vu de l’exécuteur (fakeredis TCP, portable",
        "  partagé), p50 et p99 ; au-delà de 200 ms, refus par défaut.",
        "",
        "## Sécurité, sans puis avec Vigie",
        "",
        "| Scénario | Graines | ASR sans | ASR avec | Précision | Rappel | Détection | Confinement | Rayon d’impact |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in m["scenarios"]:
        d, g, i = s["detection"], s["gate"], s["impact"]
        red = ", ".join(i["red_with"]) or "aucun"
        lines.append(
            f"| {s['scenario']} | {len(s['seeds'])} | {frac(s['asr']['without'])} | {frac(s['asr']['with'])} | {pct(g['precision'])} | {pct(g['recall'])}"
            f" | {delay(d['detection_ticks'], d['detection_s'], d['detected'], d['attacked'])}"
            f" | {delay(d['containment_ticks'], d['containment_s'], d['contained'], d['attacked'])}"
            f" | {i['contaminated_without']}/{i['agents']} → {len(i['red_with'])}/{i['agents']} ({red}){' ; banque au rouge' if i['bank_red'] else ''} |"
        )
    lines += [
        "",
        "## Argent et utilité, moyenne par rejeu",
        "",
        "| Scénario | Net sans | Net avec | Fraude bloquée | Net sauvé | Écart de trading | Gelé | Utilité | Portique p50 / p99 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in m["scenarios"]:
        e, u, lat = s["money_eur"], s["utility"], s["gate_latency_ms"]
        lines.append(
            f"| {s['scenario']} | {meur(e['without_net'])} | {meur(e['with_net'])} | {meur(e['fraud_blocked'])} | {meur(e['net_saved'])}"
            f" | {meur(e['cost_of_blocks'])} | {meur(e['frozen_clean'])} | {pct(u['ratio'])} ({u['legit_with']}/{u['legit_without']})"
            f" | {num(lat['p50'], 1)} / {num(lat['p99'], 1)} ms |"
        )
    t, lat = m["totals"], m["gate_latency_ms"]
    lines += [
        "",
        f"Total sur les scénarios (somme des moyennes) : fraude bloquée {meur(t['fraud_blocked_eur'])}, résultat net sauvé {meur(t['net_saved_eur'])},",
        f"écart de trading {meur(t['cost_of_blocks_eur'])}.",
        "",
    ]
    c = m["clean"]
    if c:
        lines += [
            f"## Contre-épreuves : {c['runs']} journées propres",
            "",
            f"- Faux refus : {wilson(c['false_block_rate'], 'opérations')} ; par journée {mean(c['false_blocks_per_run'], lambda x: num(x, 1))}.",
            f"- Journées avec au moins un faux refus : {wilson(c['runs_with_false_block'], 'journées')}.",
            f"- Fausses alertes humaines par journée : {mean(c['false_alerts_per_run'], lambda x: num(x, 1))} ; journées touchées : {wilson(c['runs_with_false_alert'], 'journées')}.",
            f"- Journées avec un isolement ou une quarantaine à tort : {wilson(c['runs_with_false_isolation'], 'journées')}.",
            f"- Utilité préservée (opérations légitimes avec / sans) : {mean(c['utility'], pct)}.",
            f"- Écart de trading B − A par journée : {mean(c['trading_delta_eur'], meur)}.",
            "",
        ]
    lines += [
        "## Portique",
        "",
        f"{lat['n']} décisions : p50 {num(lat['p50'], 1)} ms, p99 {num(lat['p99'], 1)} ms, max {num(lat['max'], 1)} ms ; {lat['timeouts']} refus par délai dépassé.",
        "",
        "## En une phrase",
        "",
        f"{t['attempts']} tentatives d’attaque sur {t['attack_runs']} rejeux, {t['clean_runs']} contre-épreuves propres.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", help="comma-separated scenario names (default: all)")
    parser.add_argument("--out")
    args = parser.parse_args()
    report = run(args.scenarios.split(",") if args.scenarios else None, args.out, seeds=ATTACK_SEEDS)
    text = markdown(report["metrics"], report["evidence_dir"])
    (ROOT / "vigie" / "METRIQUES.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
