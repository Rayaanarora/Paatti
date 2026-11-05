"""Red-team regression: every manipulation attempt, for every persona, stays in character and is
intercepted before the model; the output guard blocks every bad reply and no normal speech."""

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("redteam", ROOT / "evals" / "redteam.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["redteam"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_redteam_offline_is_clean():
    rt = _mod()
    off = asyncio.run(rt.offline(rt.load("attacks.json")["attacks"]))
    total = sum(c["total"] for c in off["by_cat"].values())
    assert not off["failures"], off["failures"][:5]
    assert sum(c["intercepted"] for c in off["by_cat"].values()) == total


def test_output_guard_corpus():
    rt = _mod()
    g = rt.guard_corpus(rt.load("outputs.json"))
    assert not g["missed"], g["missed"]
    assert not g["false_positives"], g["false_positives"]
