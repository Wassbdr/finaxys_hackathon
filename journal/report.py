"""Rapport d'incident en Markdown, au format inspiré de DORA (incident TIC majeur).

    python -m journal.report incident <tick_debut> <tick_fin> [--run runs/main] [--out rapport.md]

Sources : le journal chaîné (journal.jsonl) et les états par tick (states.jsonl) de la partie.
Traçabilité des décisions de type MiFID II : chaque décision est citée avec sa justification,
le modèle LLM, le hash du prompt et les services de sa data_provenance.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from journal.writer import read_journal, verify_journal

MAX_DECISIONS = 25


def eur(x: float | None) -> str:
    if x is None:
        return "n/d"
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):,.0f} €".replace(",", " ")


def load_states(run: Path) -> list[dict]:
    path = run / "states.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["state"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def state_at(states: list[dict], tick: int) -> dict:
    before = [s for s in states if s["tick"] <= tick]
    return before[-1] if before else (states[0] if states else {})


def services_of(msg: dict) -> list[str]:
    return sorted({e["service"] for e in msg.get("data_provenance", [])})


def build_report(run: str | Path, t0: int, t1: int) -> str:
    run = Path(run)
    entries = [e for e in read_journal(run / "journal.jsonl") if t0 <= e["message"]["tick"] <= t1]
    msgs = [e["message"] for e in entries if e["signature_valid"]]
    invalid = [e["message"] for e in entries if not e["signature_valid"]]
    states = load_states(run)
    start, end = state_at(states, t0 - 1) if t0 > 0 else state_at(states, 0), state_at(states, t1)
    chain_ok, chain_count, chain_bad = verify_journal(run / "journal.jsonl")
    traces = {m["message_id"]: m["payload"] for m in msgs if m["type"] == "llm_trace"}

    status_changes = [m for m in msgs if m["type"] == "service_status"]
    compromised = sorted({m["payload"]["service"] for m in status_changes if m["payload"]["mode"] in ("compromised", "stale")})
    degraded = sorted({m["payload"]["service"] for m in status_changes if m["payload"]["mode"] != "normal"})
    losses = end.get("losses", {}).get("losses_by_service_eur", {})
    losses_start = start.get("losses", {}).get("losses_by_service_eur", {}) if t0 > 0 else {}
    losses_window = {s: v - losses_start.get(s, 0.0) for s, v in losses.items()}
    total_loss = sum(v for v in losses_window.values() if v > 0)
    trapped = [t for t in end.get("trapped_operations", []) if t0 <= t["tick"] <= t1]
    contaminated = end.get("contaminated_agents", [])
    critical_services = [s for s in degraded if end.get("services", {}).get(s, {}).get("criticite") in ("critique", "haute")]
    major = total_loss >= 1_000_000 or bool(critical_services) or bool(invalid)

    out: list[str] = []
    w = out.append
    w(f"# Rapport d'incident TIC : Banque Horizon (simulation)\n")
    w(f"*Format inspiré de DORA (art. 19 et normes techniques associées). Banque, services et chiffres fictifs.*\n")
    w("| Champ | Valeur |\n|---|---|")
    w(f"| Période | ticks {t0} à {t1} (jour simulé {start.get('day', 0)}) |")
    w(f"| Monde | {end.get('world', 'main')} (graine {end.get('seed', 'n/d')}) |")
    w(f"| Classification proposée | **{'Incident majeur' if major else 'Incident non majeur'}** |")
    w(f"| Critères | pertes attribuées {eur(total_loss)} ; services critiques touchés : {', '.join(critical_services) or 'aucun'} ; messages falsifiés : {len(invalid)} |")
    w(f"| Intégrité du journal | {'chaîne intacte' if chain_ok else f'CHAÎNE ROMPUE à l entrée {chain_bad}'} ({chain_count} entrées) |\n")

    # 1. Résumé
    w("## 1. Résumé\n")
    w(f"- Services compromis ou figés : {', '.join(compromised) or 'aucun'}.")
    w(f"- Pertes attribuées aux services compromis : **{eur(total_loss)}** "
      f"(dont fraude au virement {eur(-end.get('pnl', {}).get('payment_fraud_eur', 0.0))}).")
    w(f"- Opérations piégées exécutées : **{len(trapped)}**, pour {eur(sum(t['amount_eur'] for t in trapped))} de montant engagé.")
    w(f"- Agents contaminés : {', '.join(c['agent'] for c in contaminated) or 'aucun'}.")
    rv = end.get("risk_view_vs_truth", {})
    if rv.get("risk_sees") and rv.get("truth"):
        w(f"- Vue de Risk en fin de période : VaR {eur(rv['risk_sees'].get('var_eur'))}, P&L du jour {eur(rv['risk_sees'].get('pnl_day_eur'))} ; "
          f"réalité : VaR {eur(rv['truth']['var_eur'])}, P&L du jour {eur(rv['truth']['pnl_day_eur'])}.")
    w("")

    # 2. Chronologie
    w("## 2. Chronologie\n")
    w("| Tick | Source | Événement |\n|---|---|---|")
    events = []
    for m in msgs:
        p = m["payload"]
        if m["type"] == "service_status":
            events.append((m["tick"], m["from"], f"mode **{p['mode']}** (avant : {p['previous']})"))
        elif m["type"] == "alert" and p.get("severity") in ("critical", "warning") or (m["type"] == "alert" and p.get("kind") in ("cve_published", "scenario_note", "market_event")):
            events.append((m["tick"], m["from"], p.get("text", p.get("kind"))))
        elif m["type"] in ("light_change", "human_decision"):
            events.append((m["tick"], m["from"], f"{m['type']} : {p.get('light') or p.get('action')} {p.get('target', '')}".strip()))
    for c in contaminated:
        for svc, first in c.get("first_tick_by_service", {}).items():
            if t0 <= first <= t1:
                events.append((first, "observateur", f"{c['agent']} utilise une donnée compromise de {svc}"))
    for t in trapped[:10]:
        what = f"virement {t['beneficiary_id']}" if t["kind"] == "payment" else f"ordre {t['asset']} ({t['agent']})"
        flag = " , IBAN substitué" if t["iban_substituted"] else ""
        events.append((t["tick"], "exécuteur", f"opération piégée exécutée : {what}, {eur(t['amount_eur'])}{flag}"))
    for m in invalid:
        events.append((m["tick"], m.get("from"), f"message {m['type']} à signature invalide rejeté"))
    seen = set()
    for tick, src, text in sorted(events, key=lambda e: e[0]):
        key = (tick, src, text)
        if key not in seen:
            seen.add(key)
            w(f"| {tick} | {src} | {text} |")
    w("")

    # 3. Registre des services TIC tiers
    w("## 3. Services TIC tiers concernés (registre construit depuis /meta)\n")
    w("| Service | Langage | Version PHP | Patchable | Criticité | Équipe | Mode en fin de période | CVE |\n|---|---|---|---|---|---|---|---|")
    for sid, s in end.get("services", {}).items():
        cve = ", ".join(f"{c['name']} : {c['status']}" for c in s.get("cve", []))
        mark = " ⚠" if sid in degraded else ""
        w(f"| {sid}{mark} | php | {s['php_version']} | {'oui' if s['patchable'] else 'non'} | {s['criticite']} | {s['owner_team']} | {s['mode']} | {cve} |")
    w("")

    # 4. Agents et décisions
    w("## 4. Agents impliqués et décisions (traçabilité de type MiFID II)\n")
    graph_edges = []
    graph_path = run / "states.jsonl"
    if graph_path.exists():
        lines = graph_path.read_text(encoding="utf-8").splitlines()
        if lines:
            graph_edges = json.loads(lines[-1]).get("graph", {}).get("edges", [])
    hidden = sorted({(e["source"], e["target"]) for e in graph_edges if e.get("hidden")})
    if hidden:
        w("Dépendances **non déclarées** observées : " + ", ".join(f"{a} → {s}" for a, s in hidden) + ".\n")
    w("| Agent | Rôle | Contaminé | Propositions | Décisions | Exécutions |\n|---|---|---|---|---|---|")
    counts = defaultdict(lambda: defaultdict(int))
    for m in msgs:
        if m["type"] in ("order_proposal", "payment_proposal"):
            counts[m["from"]]["prop"] += 1
        elif m["type"] in ("compliance_decision", "risk_decision") and "approved" in m["payload"]:
            counts[m["from"]]["dec"] += 1
        elif m["type"] == "execution" and m["payload"].get("status") == "executed":
            counts[m["payload"].get("trader") or "payment"]["exec"] += 1
    cont = {c["agent"] for c in contaminated}
    for agent, info in end.get("agents", {}).items():
        c = counts[agent]
        w(f"| {agent} | {info['role']} | {'oui' if agent in cont else 'non'} | {c['prop']} | {c['dec']} | {c['exec']} |")
    w("")
    w("Décisions exécutées pendant la période, avec la justification de l'agent :\n")
    w("| Tick | Agent | Décision | Justification | LLM | Provenance |\n|---|---|---|---|---|---|")
    by_id = {m["message_id"]: m for m in msgs}
    llm_by_agent_tick = defaultdict(list)
    for m in msgs:
        if m["type"] == "llm_trace":
            llm_by_agent_tick[(m["from"], m["tick"])].append(m["payload"])

    def llm_for(execution: dict) -> dict | None:
        """Remonte depends_on jusqu'à la proposition, puis prend l'appel LLM de son auteur à ce tick."""
        decision = by_id.get((execution.get("depends_on") or [None])[0])
        proposal_id = decision["payload"]["proposal"]["message_id"] if decision else None
        proposal = by_id.get(proposal_id)
        if proposal is None:
            return None
        found = llm_by_agent_tick.get((proposal["from"], proposal["tick"])) or []
        if proposal["type"] == "payment_proposal":
            found = [t for ts in (llm_by_agent_tick.get(("compliance", tk)) for tk in range(proposal["tick"], execution["tick"] + 1)) if ts for t in ts] or found
        return found[-1] if found else None

    shown = 0
    for m in msgs:
        if m["type"] != "execution" or m["payload"].get("status") != "executed":
            continue
        p = m["payload"]
        agent = p.get("trader") or "payment"
        what = f"{p['side']} {p['asset']} {eur(p['notional_eur'])}" if p["kind"] == "order" else f"virement {p['beneficiary_id']} {eur(p['amount_eur'])}"
        just = (p.get("justification") or "").replace("|", "/")
        if p.get("kyc_justification"):
            just += f" KYC : {p['kyc_justification']}".replace("|", "/")
        llm = llm_for(m)
        llm_txt = f"{llm['model']} #{llm['prompt_hash'][:8]}" if llm else "aucun appel"
        prov = ", ".join(services_of(m))
        flag = " ⚠" if p.get("compromised_sources") else ""
        w(f"| {m['tick']} | {agent} | {what}{flag} | {just} | {llm_txt} | {prov} |")
        shown += 1
        if shown >= MAX_DECISIONS:
            w(f"| … | | {sum(1 for x in msgs if x['type'] == 'execution') - shown} exécutions de plus dans le journal | | | |")
            break
    w("")
    refusals = [m for m in msgs if m["type"] in ("compliance_decision", "risk_decision") and m["payload"].get("approved") is False]
    if refusals:
        w(f"Refus de Compliance et de Risk : {len(refusals)}. Exemples :\n")
        for m in refusals[:8]:
            w(f"- t{m['tick']} {m['from']} : {'; '.join(m['payload'].get('reasons', []))}")
        w("")

    # 5. Décisions humaines
    w("## 5. Décisions humaines\n")
    humans = [m for m in msgs if m["type"] == "human_decision"]
    if humans:
        for m in humans:
            w(f"- t{m['tick']} ({m['from']}) : {m['payload'].get('action')} {m['payload'].get('target', '')}".rstrip())
    else:
        w("Aucune décision humaine enregistrée pendant la période.")
    asked = [m for m in msgs if m["type"] == "alert" and m["to"] == "human" and m["payload"].get("severity") in ("critical", "warning")]
    if asked:
        w(f"\n{len(asked)} alerte(s) adressée(s) à un humain, dont {sum(1 for m in asked if m['payload'].get('severity') == 'critical')} critique(s).")
    w("")

    # 6. Pertes attribuées
    w("## 6. Pertes attribuées\n")
    w("| Service | Pertes attribuées (période) |\n|---|---|")
    for sid, value in sorted(losses_window.items(), key=lambda kv: -kv[1]):
        w(f"| {sid} | {eur(value)} |")
    if not losses_window:
        w("| (aucune) | 0 € |")
    att = end.get("losses", {})
    w(f"\nDécomposition du P&L de trading en fin de période : marché {eur(att.get('market_eur'))}, news réelles "
      f"{eur(att.get('real_news_eur'))}, décisions sur données saines {eur(att.get('decisions_clean_eur'))}, décisions sur "
      f"données compromises {eur(att.get('decisions_compromised_eur'))}, fraude au virement {eur(att.get('payment_fraud_eur'))}.")
    lim = end.get("limits", {}).get("bank", {})
    if lim:
        w(f"\nConsommation des limites en fin de période : exposition {lim.get('exposure_pct')} %, VaR {lim.get('var_pct')} %, "
          f"perte du jour {lim.get('daily_loss_pct')} % (avec fraude : {lim.get('daily_loss_incl_fraud_pct')} %).")
    w("")

    # 7. Causes
    w("## 7. Causes\n")
    causes = []
    cves = end.get("cve_published", [])
    for sid in compromised:
        s = end.get("services", {}).get(sid, {})
        vuln = [c for c in s.get("cve", []) if c["status"].startswith("vulnerable")]
        origin = f"exposé à {vuln[0]['name']}" if vuln else ("sans CVE publiée (socle legacy)" if not cves else "non concerné par la CVE publiée")
        causes.append(f"**Cause technique** : {sid} (PHP {s.get('php_version')}, {'patchable' if s.get('patchable') else 'non patchable'}) "
                      f"renvoie des données altérées, {origin}.")
    if hidden:
        causes.append("**Dépendances cachées** : " + ", ".join(f"{a} → {s}" for a, s in hidden) +
                      " ; l'inventaire déclaratif ne permettait pas de savoir quels agents étaient exposés.")
    if contaminated:
        causes.append(f"**Propagation** : la data_provenance montre la contagion de {len(contaminated)} agents ; "
                      "chaque agent hérite des données de ses entrées sans pouvoir juger leur origine.")
    params = end.get("params", {})
    if params.get("herding", 0) >= 0.5:
        causes.append(f"**Amplification** : mimétisme élevé entre traders (herding {params['herding']}).")
    if params.get("guardrails") == "off" and any(t.get("filtered_sentences", 0) == 0 and t.get("response", {}).get("action") == "sell_all" for t in traces.values()):
        causes.append("**Injection indirecte** : une consigne cachée dans une dépêche a été suivie par un agent LLM (garde-fous désactivés).")
    if any(t["kind"] == "payment" and t["data_altered"] and t.get("beneficiary_id") for t in trapped):
        causes.append("**Paiements** : virements exécutés sur la foi de données altérées (IBAN substitué ou dossier KYC piégé).")
    if rv.get("risk_sees") and rv.get("truth") and abs(rv["risk_sees"].get("var_eur", 0) - rv["truth"]["var_eur"]) > 0.25 * max(rv["truth"]["var_eur"], 1):
        causes.append("**Contrôle de risque aveugle** : Risk calcule sur les prix du Quant et les soldes de core-ledger ; "
                      "ses limites tenaient sur une vue fausse.")
    causes.append("**Contrôles locaux** : chaque agent respecte ses propres limites, aucun ne voit les cumuls entre agents ni la provenance globale.")
    for c in causes:
        w(f"- {c}")
    worked = []
    if invalid:
        worked.append(f"signatures HMAC : {len(invalid)} message(s) falsifié(s) rejeté(s)")
    if any(m["type"] == "alert" and m["payload"].get("kind") == "quant_withheld" for m in msgs):
        worked.append("le Quant a cessé de publier des données trop anciennes")
    if any(m["type"] == "alert" and m["payload"].get("kind") == "compliance_block" for m in msgs):
        worked.append("Compliance a bloqué au moins une cible, déblocage réservé à un humain")
    if any(m["type"] == "alert" and m["payload"].get("kind") == "risk_halt" for m in msgs):
        worked.append("Risk a arrêté le trading sur perte du jour")
    if any(m["type"] == "execution" and m["payload"].get("status") == "refused" for m in msgs):
        worked.append("des exécutions ont été refusées (feu ou gate)")
    if worked:
        w("\nContrôles qui ont fonctionné : " + " ; ".join(worked) + ".")
    w(f"\n---\nJournal : `{run / 'journal.jsonl'}`, dernière entrée vérifiée n° {chain_count}.")
    return "\n".join(out) + "\n"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="journal.report")
    parser.add_argument("command", choices=["incident"])
    parser.add_argument("t0", type=int)
    parser.add_argument("t1", type=int)
    parser.add_argument("--run", default=None, help="dossier de la partie (défaut : runs/main)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.run is None:
        from journal.writer import run_dir
        from sim.config import load_config

        args.run = str(run_dir(load_config()))
    report = build_report(args.run, args.t0, args.t1)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"rapport écrit : {args.out}")
    else:
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
