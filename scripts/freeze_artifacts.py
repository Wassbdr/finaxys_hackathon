"""Write docs/frozen-artifacts.json (run once, then commit and tag `stage4-frozen`).

python scripts/freeze_artifacts.py [--out docs/frozen-artifacts.json]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sentinel.eval.freeze import write_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/frozen-artifacts.json"))
    args = parser.parse_args()
    manifest = write_manifest(args.out)
    for rel, digest in manifest.items():
        print(f"{digest[:16]}  {rel}")
    print(f"wrote {args.out} ({len(manifest)} files)")


if __name__ == "__main__":
    main()
