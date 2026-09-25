"""Keep ``examples/django_shop`` working: drive it end to end in a subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO / "examples" / "django_shop"


@pytest.mark.skipif(not _EXAMPLE.is_dir(), reason="example not present (installed package)")
def test_example_shop_flow() -> None:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(_EXAMPLE), str(_REPO)]),
        "RATE_LIMIT_PER_IP_PER_MINUTE": "1000",
    }
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script path
        [sys.executable, str(Path(__file__).with_name("_example_smoke.py"))],
        cwd=_EXAMPLE,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "example shop OK" in result.stdout
