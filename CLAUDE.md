# Paatti

Scam-baiting honeypot for Indian "digital arrest" scams. A 72-year-old Tamil grandmother persona keeps the caller talking, gets them to state and confirm mule account details, and produces a Mule Intel Report (PDF + JSON) for bank fraud teams and cyber cells.

## Commands
```bash
cd backend && pip install -e '.[dev]' && pytest --cov          # backend tests (>= 80% coverage)
python evals/run_evals.py                                      # offline eval on scripted calls
python evals/run_evals.py --llm                                # with Claude (needs ANTHROPIC_API_KEY)
cd frontend && npm install && npm test && npm run build        # dashboard
cd backend && uvicorn paatti.main:app --reload                 # API on :8000 (serves frontend/dist)
cd frontend && npm run dev                                     # dev UI on :5173, proxies /api and /ws
```

## Claude Code setup
This repo vendors the relevant parts of [everything-claude-code](https://github.com/alphachoi/everything-claude-code) (MIT, see `.claude/ECC-LICENSE`):
- `.claude/rules/` — coding style, testing (TDD, 80% coverage), security, git workflow. Follow them.
- `.claude/agents/` — planner, architect, tdd-guide, code-reviewer, security-reviewer, e2e-runner, build-error-resolver, doc-updater, refactor-cleaner.
- `.claude/commands/` — `/plan`, `/tdd`, `/eval`, `/verify`, `/code-review`, `/e2e`, `/test-coverage`, `/checkpoint`, `/build-fix`, `/orchestrate`, `/refactor-clean`, `/update-docs`.
- `.claude/skills/` — tdd-workflow, eval-harness, verification-loop, backend-patterns, frontend-patterns, security-review, coding-standards, strategic-compact, iterative-retrieval, plus the project skill **paatti-guidelines** (read it before changing extraction, read-back, or reports).

ECC's Java/Go/Spring/ClickHouse material and its Node hook scripts were not copied; they don't apply to this Python + React stack.

## Rules specific to this project
- Never commit API keys. Config is environment-only (`.env.example`).
- Don't weaken the verbatim-quote check, the read-back gate, or the NCRP exclusion of unconfirmed identifiers (see the paatti-guidelines skill).
- Any change to extraction or persona needs an eval run; add a script to `evals/scripts/` for new scam patterns.
- Ethics: honeypot on our own line only; no real person's voice or data; legal review before any pilot.
