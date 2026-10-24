import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HMAC_MASTER_SECRET", "test-secret")

from sim.config import load_config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return load_config(overrides={"runs_dir": str(tmp_path / "runs"), "llm": {"cache": False}})
