"""Regression eval (eval-harness): the offline pipeline must keep passing the scripted calls."""

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_scripted_calls_pass_offline():
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_evals"] = mod
    spec.loader.exec_module(mod)
    results = asyncio.run(mod.run_all(mod.load_scripts(ROOT / "evals" / "scripts"), use_llm=False))
    _, passed = mod.summarise(results, "regression")
    failed = [(r.id, k, r.details[k]) for r in results for k, v in r.checks.items() if not v]
    assert passed and not failed, failed
