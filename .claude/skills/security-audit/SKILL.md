---
name: security-audit
description: Perform a security audit of the waiting_room codebase or a diff, covering token/HMAC handling, cookies, redirects, IP trust, Redis/Lua safety, Django views, secrets and dependencies. Use when asked for a security audit, security review, threat model, vulnerability check, or before a release.
model: opus
argument-hint: "[path | git-range | 'full'] (defaults to the working-tree diff)"
allowed-tools: Read, Grep, Glob, Bash
---

# Security Audit

Audit as an attacker who wants to **skip the queue, hold admission slots, forge tokens,
or knock the room over**. Every finding needs a realistic exploit path and a fix.

## 1. Scope

- `$ARGUMENTS` empty → `git diff HEAD`; `full` → entire `waiting_room/` package plus
  repo-level config; a path or git range → that.
- Always read the full file around a change, and trace untrusted input from the request
  to where it is used.

## 2. Automated checks

```bash
ruff check waiting_room --select S          # bandit rules
pip-audit 2>/dev/null || echo "pip-audit not installed"
git ls-files | grep -Ei '(^|/)\.env|secret|\.pem$|\.key$|credentials'
git log -p --all -S 'SECRET' -- . ':!waiting_room/tests' | head -200
```

Report tracked secret-bearing files (for example anything under `.envs/`) as a finding
even when the values look like placeholders. Never print secret values in the report.

## 3. Threat checklist

### 3.1 Admission tokens (`core/tokens.py`)
- Signature comparison uses `hmac.compare_digest`, never `==`.
- HMAC uses SHA-256 or stronger; the key comes from settings, has a minimum length,
  and is never logged or put in exception messages.
- Payload is authenticated **before** it is parsed or trusted.
- Tokens carry and enforce: expiry, room binding, fingerprint binding, and a
  single-use or bounded-reuse guarantee (replay protection).
- A token for room A is rejected by room B.
- Base64 / JSON decoding errors map to "invalid token", never to a 500 or bypass.

### 3.2 Cookies
- `HttpOnly`, `Secure` (configurable, default on outside DEBUG), `SameSite=Lax` or stricter.
- Cookie `max_age` never exceeds token TTL.
- Session-id cookies are random (`uuid4` / `secrets`), never sequential or derived.

### 3.3 Open redirect (`next` parameter)
- Every `next` / redirect target from `request.GET` / `request.POST` is validated with
  `django.utils.http.url_has_allowed_host_and_scheme` (with `require_https` when
  appropriate) or restricted to relative paths. `//evil.com` and `\\evil.com` and
  `javascript:` must be rejected.
- Rendering `next` into templates is autoescaped; no `|safe` on user data.

### 3.4 Client identity and IP trust (`adapters/django/_request.py`)
- `X-Forwarded-For` is honoured only from configured trusted proxies; otherwise an
  attacker spoofs IP to bypass rate limits, allowlists and fingerprint binding.
- The right-most untrusted hop is used, not the left-most client-supplied value.
- IP allowlists compare parsed `ipaddress` objects / networks, not string prefixes.
- User-agent hashing uses a real hash (SHA-256), not Python `hash()`.

### 3.5 Queue integrity and abuse
- A client cannot enqueue unlimited sessions (rate limit per IP / fingerprint).
- A client cannot improve its position by re-enqueueing, forging `sid`, or racing.
- `sid` lookups for another user's session do not leak position or admit them.
- Kill switch and fail-closed paths cannot be bypassed by a crafted path
  (trailing slash, encoded characters, case, `..`), so PROTECT prefix matching must
  use the normalised `request.path`.
- Allowlisted/staff bypasses check `is_authenticated` and are not triggered by headers.

### 3.6 Redis and Lua
- Key names are built from validated room names only; user input never becomes a key
  without an allowlist (prevents cross-room reads/writes and key injection).
- Lua scripts take keys via `KEYS[]` and treat `ARGV` as data; no `redis.call` on
  dynamically-constructed commands.
- No `KEYS *`, `FLUSHDB`, or `FLUSHALL` in runtime code; destructive management
  commands require explicit confirmation (`--yes`).
- Every key has a TTL or is cleaned by reclaim; no unbounded growth an attacker can
  drive (memory exhaustion DoS).
- Redis URL and password come from settings/env, not source.

### 3.7 Django views and admin
- State-changing views are POST-only and CSRF-protected (or documented as exempt
  with a compensating control).
- SSE endpoint caps connection lifetime and per-client connections.
- JSON endpoints do not leak internal state (other sessions, capacity internals,
  stack traces) and return uniform errors.
- Admin actions (kill switch) require staff + the right permission; they are audited.
- Templates never use `|safe`, `mark_safe`, or `autoescape off` on request data.
- `health` endpoint exposes nothing sensitive.

### 3.8 Failure modes
- Backend outage → behaviour matches the configured fail-open / fail-closed mode, and
  fail-open is never the silent default for protected write paths.
- Exceptions in signal/audit handlers cannot break the request, and cannot be used to
  skip the queue.

### 3.9 Logging and data handling
- No tokens, secrets, full cookies, or raw passwords in logs, metrics labels, or
  `AdmissionEvent.payload`.
- IPs / user IDs stored for audit are documented (privacy / retention).
- Metric label values are bounded (no per-session labels, avoids cardinality DoS).

### 3.10 Python hazards
- No `eval`, `exec`, `pickle`, `yaml.load` (non-safe), `subprocess(shell=True)`,
  `tempfile.mktemp`, or `random` for anything security-relevant (use `secrets`).
- `assert` is never used for security checks (stripped under `-O`).

### 3.11 Supply chain
- Dependency floors in `pyproject.toml` exclude versions with known CVEs.
- New dependencies are justified; optional extras stay optional.

## 4. Verify

For each candidate: trace the exact request path, confirm no upstream control blocks
it, and write the attack as concrete steps. Unconfirmed items go under
"Hardening suggestions", never under "Vulnerabilities".

## 5. Report

```
## Summary
<one paragraph: overall posture, count by severity>

## Vulnerabilities
### [CRITICAL|HIGH|MEDIUM|LOW] Title (path/file.py:LINE) (CWE-XXX)
**Attack:** step-by-step from the attacker's side.
**Impact:** what they gain (queue skip, token forgery, DoS, data exposure).
**Fix:** minimal code change.

## Hardening suggestions
- ...

## Checked and clean
- Short list of areas reviewed with no issues (so coverage is visible).
```

Describe classes of weakness and fixes. Do not produce weaponised exploit code.
