broker: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m bus.broker
world: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m sim.world
journal: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m journal.writer run
api: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m api.app
svc_market_feed: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m services.run market-feed
svc_news_wire: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m services.run news-wire
svc_kyc_docs: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m services.run kyc-docs
svc_payment_gateway: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m services.run payment-gateway
svc_core_ledger: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m services.run core-ledger
quant: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.quant
trader_1: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.trader --id trader-1
trader_2: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.trader --id trader-2
trader_3: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.trader --id trader-3
compliance: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.compliance
risk: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.risk
payment: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m agents.payment
vigie: env HORIZON_CONFIG=runs/vigie-config.yaml HORIZON_NO_LLM=1 python -m vigie
