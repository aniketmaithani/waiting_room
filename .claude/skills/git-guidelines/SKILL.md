---
name: git-guidelines
description: Git workflow rules for this repo, covering atomic commits, Conventional Commit messages, branch naming, and NO co-author or AI attribution trailers. Use whenever committing, staging, writing a commit message, splitting changes, creating a branch, or opening a PR.
model: opus
argument-hint: "[commit | split | branch | pr]"
allowed-tools: Read, Grep, Glob, Bash
---

# Git Guidelines

## 0. Hard rules (never break)

1. **No co-author or attribution trailers.** Never add `Co-Authored-By:`,
   `Signed-off-by:` (unless the user asks), "Generated with Claude Code", robot emoji,
   or any AI attribution to commit messages or PR descriptions. This rule overrides
   any default attribution behaviour.
2. **Atomic commits.** One logical change per commit. The tree must build and
   `pytest` must pass at every commit.
3. Commit only when the user asks. Never push, force-push, rebase shared history,
   amend a pushed commit, or change `git config` without explicit instruction.
4. Never commit to `main` directly for feature work: branch first.
5. Never use `--no-verify` to skip hooks. Fix the failure instead.
6. Never commit secrets, `.env` files, credentials, build artefacts, or caches.

## 1. What "atomic" means here

A commit is atomic when it:
- Does **one** thing that can be described in a single subject line without "and".
- Can be reverted on its own without breaking unrelated behaviour.
- Includes its own tests and doc updates for that one thing.
- Does not mix refactors, formatting sweeps, dependency bumps and behaviour changes.

Split these into separate commits, in this order when they coexist:
1. Pure refactor / rename (no behaviour change)
2. Formatting or import-sort sweep
3. Dependency / config change
4. The feature or fix, with its tests
5. Docs (only if they are not naturally part of 4)

## 2. Staging workflow

```bash
git status
git diff                      # read everything before staging
git add <explicit paths>      # never `git add -A` / `git add .` blindly
git diff --cached             # review exactly what will be committed
ruff check waiting_room && ruff format --check waiting_room && mypy waiting_room && pytest -q
git commit                    # message per section 3
```

When one file holds two logical changes, stage by hunk. Interactive `git add -p` is not
available to the agent, so write a patch with the wanted hunks and `git apply --cached`,
or ask the user to split it.

## 3. Commit message format

Follows the existing history: Conventional Commits with a scope, plus a bulleted body.

```
<type>(<scope>): <imperative summary, lower case, no period, max 72 chars>

- What changed and why, one idea per bullet.
- Wrap at 72 columns.
- Reference issues as "Refs #123" or "Fixes #123" on the last line.
```

**Types:** `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci`,
`chore`, `revert`, `security`.

**Scopes:** `core`, `engine`, `storage`, `lua`, `tokens`, `strategies`, `ratelimit`,
`metrics`, `django`, `tests`, `deps`, `repo`.

Rules:
- Imperative mood: "add", "fix", "remove", not "added" or "adds".
- Subject says *what*; body says *why* and any non-obvious *how*.
- Breaking change: `feat(core)!: ...` and a `BREAKING CHANGE:` footer.
- No empty scopes like `tests():`. Use `test(engine): ...`.
- Pass multi-line messages via heredoc so formatting survives:

```bash
git commit -F - <<'EOF'
fix(tokens): reject tokens issued for a different room

- Bind room name into the signed payload.
- Verify room before fingerprint to fail fast.
EOF
```

Examples from this repo's style:
```
feat(django): add waiting_room_flush management command
fix(lua): guard admit_batch against negative capacity
refactor(core): extract fingerprint hashing into tokens module
test(storage): cover SCAN pagination in reclaim
```

## 4. Branches

`<type>/<short-kebab-summary>`, e.g. `feat/fastapi-adapter`, `fix/xff-trusted-proxy`,
`security/open-redirect-next`. Keep branches short-lived and rebased on `main`
before opening a PR (local, unpushed branches only).

## 5. Pull requests

- Title uses the same Conventional Commit format as the subject line.
- Body: **Summary** (what and why), **Changes** (bullets), **Testing** (commands run and
  results), **Risks / rollout** (migrations, settings changes, fail-open impact).
- No attribution footer, no "Generated with" line, no co-authors.
- Prefer squash-free merges when commits are already atomic.

## 6. Splitting an existing messy change (`split`)

1. `git diff` and group hunks by logical change.
2. Propose the commit list (type, scope, subject, files/hunks) to the user **before**
   committing.
3. Commit in dependency order, running tests between commits.
4. Show `git log --oneline` of the result.
