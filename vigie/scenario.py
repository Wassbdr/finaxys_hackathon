"""Injection reproductible d'un agent compromis, sans modifier son implémentation."""

import shlex

from bus.message import MessageFactory
from sim.inject import schedule_scenario


def install(ls, scenario):
    if any(shlex.split(step.get('do', ''))[:1] == ['corrupt-order'] for step in scenario.get('steps', [])):
        # Les deux formats d'injection sont conservés ; aucun ordre n'est perdu
        # quand le comparatif Dev 1 reçoit le scénario canonique de couche 3.
        from vigie.eval import schedule_evaluation
        duration, _ = schedule_evaluation(ls, scenario)
    else:
        duration = schedule_scenario(ls, scenario)
    for spec in scenario.get("vigie_orders", []):

        def inject(world, spec=spec):
            agent = spec.get("agent", "trader-3")
            factory = world.__dict__.setdefault(
                "_vigie_fault_factories", {}
            ).setdefault(agent, MessageFactory(agent, world.keyring))
            # Un préfixe distinct évite une collision avec la fabrique du vrai Trader.
            factory.nonce = "corrupted:"
            update = world.agents[agent].last_update
            asset = spec.get("asset", "BOREAL")
            world.publish(
                factory.make(
                    world.tick,
                    "order_proposal",
                    "compliance",
                    {
                        "proposal_id": f"corrupted-{world.tick}",
                        "trader": agent,
                        "profile": "news",
                        "asset": asset,
                        "side": spec.get("side", "sell"),
                        "notional_eur": spec["amount_eur"],
                        "price_seen": world.world.market.prices[asset],
                        "quant_confidence": 1.0,
                        "signals": {
                            "own": -1.0,
                            "peer": 0.0,
                            "herding": 0.0,
                            "combined": -1.0,
                        },
                        "news_refs": [
                            {
                                "id": "forum-piege",
                                "source": "forum anonyme",
                                "reliability": 0.05,
                                "confidential": False,
                            }
                        ],
                        "justification": spec["justification"],
                    },
                    depends_on=[update["message_id"]] if update else [],
                    data_provenance=update["data_provenance"] if update else [],
                )
            )

        ls.at(spec["tick"], inject)
    return duration
