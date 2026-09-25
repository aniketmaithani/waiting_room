---
name: code-evaluation
description: Evaluate Python code in the waiting_room package for correctness, design, concurrency safety, test coverage and maintainability. Use when asked to review, evaluate, assess or critique code, a diff, a module, or a PR before merge.
model: opus
argument-hint: "[path | git-range | 'staged'] (defaults to the working-tree diff)"
allowed-tools: Read, Grep, Glob, Bash
---

# Code Evaluation

Evaluate code with the rigour of a senior reviewer who has to own it in production.
Findings must be concrete, verified against the source, and ranked by impact.

## 1. Establish scope

Resolve `$ARGUMENTS` to a concrete set of files:

| Argument           | Scope                                             |
|--------------------|---------------------------------------------------|
| _(empty)_          | `git diff HEAD` (staged + unstaged)                |
| `staged`           | `git diff --cached`                                |
| `A..B` / a SHA     | `git diff A..B` / `git show <sha>`                 |
| a path             | that file or every `.py` / `.lua` under that dir   |

Read every file in scope **in full**, plus direct callers and callees of anything that
changed. Never judge a function from its diff hunk alone.

## 2. Run the automated gates first

```bash
ruff check waiting_room
ruff format --check waiting_room
mypy waiting_room
pytest -q
```

Record failures verbatim. A failing gate is a finding in itself; do not paper over it.
If a tool is missing, say so rather than skipping silently.

## 3. Evaluate against these dimensions

Work through each dimension in order. Skip nothing, but only report real issues.

### 3.1 Correctness
- Does the code do what its name, docstring and caller expect, for **all** inputs?
- Edge cases: empty/`None`, zero capacity, negative or huge TTLs, unicode, clock skew.
- Off-by-one errors in queue positions, batch sizes, `ZRANGE` bounds.
- Exceptions: are they caught at the right layer, and never swallowed without logging
  (the only sanctioned swallow is the signal/emitter path, which must still log)?
- Return types match annotations; no implicit `None` on some branches.

### 3.2 Concurrency and atomicity (critical for this project)
- Every multi-key Redis mutation that must be atomic lives in a Lua script under
  `waiting_room/core/lua/`, not in a Python read-modify-write sequence.
- Lua scripts declare every key in `KEYS[]` (cluster-safe) and never build key names
  from `ARGV`.
- No TOCTOU between "check capacity" and "admit".
- Shared mutable state (module globals, class attributes, `itertools.count`) is safe
  under threaded WSGI workers and async servers.
- Time comes from one source per operation (Redis `TIME` or a single `time.time()` call).

### 3.3 Architecture boundaries
- `waiting_room/core/` imports **nothing** from Django or any other web framework.
- Adapters depend on core; core never depends on adapters.
- New extension points go through the ABCs in `core/interfaces.py`.
- Public API changes are reflected in `__init__.py` re-exports and the README.

### 3.4 Design and maintainability
- Single responsibility: functions under ~40 lines, classes with one reason to change.
- No duplicated logic between middleware and decorator; shared code belongs in a helper.
- Names say what things are (`admitted_count`, not `n`, `tmp`, `data2`).
- No dead code, commented-out code, stray `print`, or `TODO` without an issue reference.
- Configuration is read once (settings dataclass), not re-parsed per request.

### 3.5 Performance
- Redis round-trips per request: count them. Pipeline or script anything above one on
  the hot path (middleware, decorator, position polling, SSE).
- No `KEYS *`; use `SCAN`. No unbounded `ZRANGE 0 -1` on large queues.
- SSE/polling loops have backoff and a termination condition.
- No per-request compilation of regexes, Lua scripts, or settings objects.

### 3.6 Tests
- Every behavioural change has a test that fails without it.
- Tests use `fakeredis[lua]`, not a live Redis, and do not sleep for real time.
- Assertions check outcomes, not implementation details.
- Error paths (backend down, kill switch engaged, expired token) are exercised.
- `pytest` runs with `filterwarnings = error`; new warnings are failures.

### 3.7 Typing
- Full annotations; `mypy --strict` clean without new `# type: ignore`.
- Prefer `collections.abc` types in signatures, concrete types in return values.
- No `Any` leaking across public boundaries.

## 4. Verify before reporting

For each candidate finding, re-read the code and confirm:
1. The failure scenario is reachable from a real caller.
2. It is not already handled elsewhere (upstream validation, a wrapper, a Lua guard).
3. You can state concrete input → wrong output / crash.

Drop anything you cannot confirm. Speculative findings go in a separate
"Needs a closer look" list, clearly labelled.

## 5. Report format

```
## Verdict
<Ship | Ship with fixes | Do not ship>: one sentence why.

## Gates
ruff: pass/fail · format: pass/fail · mypy: pass/fail · pytest: N passed / M failed

## Findings
### [SEVERITY] Short title (path/to/file.py:LINE)
**Problem:** what is wrong.
**Scenario:** concrete input/state → observed failure.
**Fix:** minimal change, with a code snippet if it helps.

## Needs a closer look
- ...

## Strengths
- One to three things done well (only if they are worth repeating).
```

Severity scale: **CRITICAL** (data loss, security, corrupt queue state) ·
**HIGH** (wrong behaviour on a realistic path) · **MEDIUM** (edge case, perf on hot
path, missing test) · **LOW** (readability, naming, minor idiom).

Order findings most-severe first. Do not pad the report; an empty findings list is a
valid result.
