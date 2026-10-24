"""Tests for the freeze manifest."""
from __future__ import annotations

import json

from sentinel.eval.freeze import build_manifest, verify_manifest, write_manifest


def _tree(root):
    for rel, content in {"models/lgbm.txt": b"model\n", "models/thresholds.json": b"{}",
                         "models/tuned_rules.json": b"{}", "models/iforest.joblib": b"\x00\x01",
                         "models/iforest_threshold.json": b"{}", "src/sentinel/features/extractor.py": b"x = 1\n",
                         "src/sentinel/detect/rules.py": b"y = 2\n", "java/src/sentinel/A.java": b"class A {}\n"}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_manifest_lists_every_frozen_file_including_java_sources(tmp_path) -> None:
    _tree(tmp_path)
    manifest = build_manifest(tmp_path)
    assert "models/lgbm.txt" in manifest and "java/src/sentinel/A.java" in manifest
    assert all(len(h) == 64 for h in manifest.values())


def test_verify_passes_until_a_frozen_file_changes(tmp_path) -> None:
    _tree(tmp_path)
    write_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path)
    assert verify_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path) == []
    (tmp_path / "models" / "thresholds.json").write_bytes(b'{"WASH": 0.5}')
    problems = verify_manifest(tmp_path / "docs" / "frozen-artifacts.json", tmp_path)
    assert problems == ["changed: models/thresholds.json"]


def test_verify_reports_missing_and_new_java_files(tmp_path) -> None:
    _tree(tmp_path)
    write_manifest(tmp_path / "m.json", tmp_path)
    (tmp_path / "models" / "lgbm.txt").unlink()
    (tmp_path / "java" / "src" / "sentinel" / "B.java").write_bytes(b"class B {}\n")
    problems = verify_manifest(tmp_path / "m.json", tmp_path)
    assert "missing: models/lgbm.txt" in problems and "new: java/src/sentinel/B.java" in problems


def test_line_endings_do_not_change_the_hash(tmp_path) -> None:
    _tree(tmp_path)
    before = build_manifest(tmp_path)["src/sentinel/detect/rules.py"]
    (tmp_path / "src/sentinel/detect/rules.py").write_bytes(b"y = 2\r\n")
    assert build_manifest(tmp_path)["src/sentinel/detect/rules.py"] == before
    assert json.loads(json.dumps(build_manifest(tmp_path)))  # serialisable
