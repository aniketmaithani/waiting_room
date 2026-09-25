# waiting_room: project instructions

Plug-and-play virtual waiting room. Framework-agnostic core in `waiting_room/core/`
(no Django imports), Django adapter in `waiting_room/adapters/django/`, Redis Lua
scripts in `waiting_room/core/lua/`.

## Gates (must pass before any commit)

```bash
ruff check waiting_room && ruff format --check waiting_room && mypy waiting_room && pytest -q
```

## Skills (all run on Opus)

- `/code-evaluation`: review a diff, file, or range for correctness, concurrency, design, tests.
- `/security-audit`: threat-focused audit (tokens, cookies, redirects, XFF, Redis/Lua, views).
- `/python-style`: PEP 8, PEP 257, import sorting, pythonic idioms; check or fix.
- `/git-guidelines`: atomic commits, Conventional Commit messages, branches, PRs.

## Always-on git rules

- **Never** add `Co-Authored-By`, "Generated with Claude Code", or any AI attribution
  to commits or PRs.
- One logical change per commit; tests pass at every commit.
- Message format: `type(scope): imperative summary` + bulleted body (see `git-guidelines`).
- Commit only when asked; never push or force-push without explicit instruction.
