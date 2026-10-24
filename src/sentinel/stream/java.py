"""Build and run the Java engine from Python."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

JAVA_DIR = Path(__file__).resolve().parents[3] / "java"
OUT_DIR = JAVA_DIR / "out"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} not found on PATH (a JDK is required)")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def available() -> bool:
    """True if javac and java are on PATH."""
    return shutil.which("javac") is not None and shutil.which("java") is not None


def build() -> Path:
    """Compile every source under java/src into java/out; returns the class directory."""
    sources = sorted((JAVA_DIR / "src").rglob("*.java"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _run([_tool("javac"), "-d", str(OUT_DIR), *map(str, sources)])
    return OUT_DIR


def _java(main_class: str, *args: object, heap: str = "3g") -> list[str]:
    return [_tool("java"), f"-Xmx{heap}", "-cp", str(OUT_DIR), main_class, *map(str, args)]


def selftest(model: Path, fixture: Path) -> str:
    """Run the Java evaluator against a Python-made fixture; raises on any mismatch."""
    return _run(_java("sentinel.SelfTest", model, fixture)).stdout


def run_engine(events_bin: Path, config: Path, model: Path, out_dir: Path, n: int | None = None) -> None:
    """Run the Java engine over an events file; writes features.csv and alerts.csv into ``out_dir``."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    args = ["run", events_bin, config, model, out_dir] + ([n] if n is not None else [])
    _run(_java("sentinel.Main", *args))


def bench_engine(events_bin: Path, config: Path, model: Path, n: int) -> dict:
    """Run the Java benchmark and return its JSON result."""
    result = _run(_java("sentinel.Main", "bench", events_bin, config, model, n))
    return json.loads(result.stdout.strip().splitlines()[-1])
