"""Hash the artifacts that determine predictions, so the test run can prove nothing changed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_FILES = ("models/lgbm.txt", "models/thresholds.json", "models/tuned_rules.json",
               "models/iforest.joblib", "models/iforest_threshold.json")
CODE_FILES = ("src/sentinel/features/extractor.py", "src/sentinel/detect/rules.py")


def _digest(path: Path) -> str:
    """sha-256 of the file with CRLF normalised to LF (so checkouts on different OSes agree)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _java_sources(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in (root / "java" / "src").rglob("*.java"))


def build_manifest(root: Path = Path(".")) -> dict[str, str]:
    """{relative path: sha-256} for the model files, extractor, rules and every Java source."""
    root = Path(root)
    return {rel: _digest(root / rel) for rel in (*MODEL_FILES, *CODE_FILES, *_java_sources(root))}


def write_manifest(path: Path, root: Path = Path(".")) -> dict[str, str]:
    """Write the manifest as JSON and return it."""
    manifest = build_manifest(root)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(path: Path, root: Path = Path(".")) -> list[str]:
    """Problems found comparing the tree against a manifest ('missing', 'changed' or 'new' lines). Empty means frozen."""
    root = Path(root)
    expected = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    for rel, digest in expected.items():
        file = root / rel
        if not file.exists():
            problems.append(f"missing: {rel}")
        elif _digest(file) != digest:
            problems.append(f"changed: {rel}")
    problems += [f"new: {rel}" for rel in _java_sources(root) if rel not in expected]
    return problems
