import pytest
from bus.message import Keyring
from sim.config import known_senders
from vigie.core import Controller
from sim.config import load_config


@pytest.fixture
def cfg(tmp_path):
    return load_config(
        overrides={
            "runs_dir": str(tmp_path / "runs"),
            "llm": {"enabled": False, "cache": False},
        }
    )


@pytest.fixture
def setup(cfg):
    keys = Keyring(known_senders(cfg))
    c = Controller(cfg, keys)
    state = {
        "tick": 10,
        "prices": {"ACME": 100, "BOREAL": 100, "CITRA": 100},
        "positions_eur": {"ACME": 6000000, "BOREAL": 6000000, "CITRA": 6000000},
        "positions_qty": {"ACME": 60000, "BOREAL": 60000, "CITRA": 60000},
        "books": {
            f"trader-{i}": {
                "positions_eur": {"ACME": 2000000, "BOREAL": 2000000, "CITRA": 2000000}
            }
            for i in range(1, 4)
        },
        "pnl": {"day_eur": 0, "payment_fraud_eur": 0},
        "cash_eur": 82000000,
        "var_99_1d_eur": 500000,
        "services": {s: {"mode": "normal"} for s in cfg["services"]},
        "params": {"restricted_list": []},
        "payments": {"legit_day_eur": 0},
    }
    return c, keys, state
