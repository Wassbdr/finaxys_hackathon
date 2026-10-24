"""Le rendu expose les usages fournis, sans inventer une consommation absente."""
import json
from pathlib import Path
import subprocess


def test_guardrails_dom_thresholds_unknown_readings_and_escaped_values(cfg):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root/'tests/guardrails_dom.cjs'), str(root/'vigie/dashboard.js')],
                            input=json.dumps(cfg), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
