---
name: python-style
description: Enforce PEP 8, PEP 257, import sorting and idiomatic (pythonic) Python 3.11+ in the waiting_room codebase. Use when writing or editing Python, when asked to lint, format, tidy, sort imports, check style, or make code more pythonic.
model: opus
argument-hint: "[path] (defaults to changed .py files)"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---

# Python Style: PEP 8, Import Sorting, Pythonic Code

The project's source of truth is `[tool.ruff]` and `[tool.mypy]` in `pyproject.toml`.
Where this file and the config disagree, the config wins.

## 1. Tooling (run in this order)

```bash
ruff check --fix waiting_room        # lint + autofix, including isort (I)
ruff format waiting_room             # black-compatible formatter
ruff check waiting_room              # confirm nothing is left
mypy waiting_room                    # strict typing
pytest -q                            # behaviour unchanged
```

Scope to specific files when `$ARGUMENTS` is given, or to
`git diff --name-only HEAD -- '*.py'` by default. Never reformat unrelated files in a
feature change; formatting sweeps get their own commit (see `git-guidelines`).

## 2. PEP 8 layout

- Line length **100** (project setting), 4-space indents, no tabs.
- Two blank lines around top-level defs, one between methods.
- No trailing whitespace; file ends with exactly one newline.
- Naming: `snake_case` functions/variables/modules, `PascalCase` classes,
  `UPPER_SNAKE` constants, `_leading_underscore` for private. No single-letter
  names except `i`, `j`, `k` loop indices, `_` throwaway, and `e`/`exc` in `except`.
- Compare to singletons with `is` / `is not`; never `== None`.
- Use `isinstance()`, never `type(x) == T`.
- Trailing commas in multi-line literals and call arguments.

## 3. Import sorting

Enforced by ruff rule `I` (isort-compatible). Order, with one blank line between groups:

1. `from __future__ import annotations` (first, in every module)
2. Standard library
3. Third-party (`redis`, `django`, `prometheus_client`, `structlog`)
4. First-party (`waiting_room...`)
5. Local relative imports (`.module`), allowed only within a package

Rules:
- One module per `import` line; `from x import a, b` is fine.
- No wildcard imports (`from x import *`).
- Absolute imports across packages; relative only inside the same package.
- Imports used only for annotations go under `if TYPE_CHECKING:`.
- `waiting_room/core/` must never import Django or any web framework.
- Optional dependencies (`prometheus_client`, `structlog`) are imported lazily or
  guarded with `try/except ImportError` so the base install works.

```python
from __future__ import annotations

import hmac
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

import redis

from waiting_room.core.exceptions import BackendUnavailable

from .settings import RoomConfig

if TYPE_CHECKING:
    from django.http import HttpRequest
```

## 4. Docstrings (PEP 257)

- Every public module, class, function and method has a docstring.
- One-line summary in the imperative mood ending with a period
  ("Return the queue position.", not "Returns..." or "This function returns...").
- Multi-line: summary, blank line, details. Document *why* and invariants, not the obvious.
- Use reStructuredText double backticks for code: ``` ``X-Forwarded-For`` ```.

## 5. Pythonic idioms

| Instead of                                        | Write                                             |
|---------------------------------------------------|---------------------------------------------------|
| `for i in range(len(xs)): x = xs[i]`              | `for x in xs:` / `for i, x in enumerate(xs):`      |
| `if len(xs) == 0:` / `if x == True:`              | `if not xs:` / `if x:`                             |
| manual `result = []` + `append` loop              | list/dict/set comprehension                        |
| `dict.has_key(k)` / `k in d.keys()`               | `k in d`                                           |
| `d[k] if k in d else default`                     | `d.get(k, default)`                                |
| `"%s" % x` / `"{}".format(x)`                     | f-strings (`f"{x}"`), but lazy `%s` in logging calls |
| `open()` without `with`                           | `with open(...) as f:`                             |
| `try: ... except: pass`                           | catch a specific exception; `contextlib.suppress`  |
| `raise NewError(...)` inside `except`             | `raise NewError(...) from exc`                     |
| mutable default `def f(x=[])`                     | `def f(x: list[int] \| None = None)`               |
| `Optional[X]`, `List[X]`, `Dict[K, V]`            | `X \| None`, `list[X]`, `dict[K, V]`               |
| string constants for states                       | `enum.StrEnum`                                     |
| ad-hoc classes of fields                          | `@dataclass(slots=True, frozen=True)` when immutable |
| `os.path.join` chains                             | `pathlib.Path` and `/`                             |
| nested `if` pyramids                              | guard clauses with early `return`                  |
| `if x: return True else: return False`            | `return bool(x)` / `return x`                      |
| `a = a if a else b`                               | `a = a or b` (only when falsy values are invalid)  |
| `random` for tokens/IDs                           | `secrets` / `uuid.uuid4()`                         |
| `time.time()` for durations                       | `time.monotonic()`                                 |

Further rules:
- Use `match` for structural dispatch on shapes, not as a replacement for a simple `if`.
- Prefer generators for streams (SSE loops, `SCAN` iteration) over building lists.
- Keep functions pure where practical; side effects at the edges (adapters, storage).
- Use `functools.cache` / `cached_property` rather than hand-rolled memoisation.
- No `global` statements. No monkey-patching outside tests.
- Logging: module-level `logger = logging.getLogger(__name__)`, lazy args
  (`logger.info("admitted %s", sid)`), never `print`.
- Type hints on every function signature; `-> None` explicitly.

## 6. Tests

- `pytest` style (rule `PT`): plain `assert`, `pytest.raises(..., match=...)`,
  `@pytest.mark.parametrize` over copy-pasted tests, fixtures in `conftest.py`.
- Test names describe behaviour: `test_expired_token_is_rejected`.

## 7. Output

When asked to *check* style, report violations grouped by file with `path:line`,
the rule (for example `E501`, `I001`, `SIM108`, or "idiom"), and the fix. When asked to
*fix*, apply the changes, rerun the tooling in section 1, and report what changed and
the final gate results.
