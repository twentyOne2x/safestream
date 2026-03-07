# Agent Map

Keep this file short. It is a map into the knowledge base, not the full manual.

## Start Here
- [Knowledge index](docs/index.md)
- [Core beliefs](docs/core-beliefs.md)
- [Architecture](ARCHITECTURE.md)
- [Plan template](docs/plans/plan-template.md)
- [Tech debt tracker](docs/plans/tech-debt-tracker.md)

## Working Rules
- Keep `AGENTS.md` concise and repo-local.
- Use `docs/` as the system of record.
- For non-trivial work, create a plan in `docs/plans/active/`.
- When work finishes, move the plan to `docs/plans/completed/` with outcome notes.
- Run `python3 scripts/knowledge_check.py` after KB edits.

## Verification
- `python3 scripts/knowledge_check.py`
