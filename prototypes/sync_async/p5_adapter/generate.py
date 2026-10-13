"""Regenerate ``_sync/`` from ``_async/`` with p4's unasync generator.

    cd prototypes/sync_async && uv run --with unasync python p5_adapter/generate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from p4_codegen.generate import generate as _generate  # noqa: E402

HERE = Path(__file__).parent


def generate(out_root: Path = HERE) -> set[Path]:
    return _generate(out_root, src_root=HERE)


if __name__ == "__main__":
    for rel in sorted(generate()):
        print(f"_sync/{rel}")
