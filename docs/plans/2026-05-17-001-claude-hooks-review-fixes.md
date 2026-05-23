# Claude Hooks — Review Fix Plan

**Created:** 2026-05-17
**Source:** Standalone code review of `.claude/hooks/` (19 hook scripts + `otel_span.py`)
**Status:** Complete

## Context

Standalone review of the Claude Code OTel hook suite surfaced one P0 (credential exfiltration via git remote URL), several P1 reliability/security issues, and a batch of P2 cleanups. This plan sequences the fixes from highest-leverage and lowest-risk first, so each unit can land independently without coordinating with the others.

The hook suite is small (each hook ≤ 50 lines, shared helper ~150 lines). Most fixes touch `otel_span.py` only; the trivial cleanups apply identically across all 18 hook scripts.

## Requirements

- **R1** — No git-remote credentials leave the machine. Any `git.origin` attribute attached to spans must have userinfo (`user:token@`) stripped before export.
- **R2** — Hook subprocess crashes must not corrupt unrelated user files. Tempfile writes in `/tmp` must be safe against symlink pre-creation by another local user (relevant on shared hosts; nice-to-have on personal laptops).
- **R3** — PreToolUse-class hooks must not surface unhandled `OSError` when `/tmp` is full or unwritable. Tool calls should continue without instrumentation rather than crash visibly in the transcript.
- **R4** — No dead/no-op code in the hook preamble. The repeated `os.path.expanduser` wrapping of an already-absolute path must be removed across all 18 hooks.
- **R5** — Diagnostic tools that dump environment variables must not be loadable as a configured hook by accident, and must redact credential-shaped values if they ever run.
- **R6** — Hook behavior on malformed JSON stdin must be observable (one stderr line is enough); silent empty spans must stop being the default outcome.
- **R7** — `__pycache__/` must be gitignored in the hooks directory.

## Implementation Units

Each unit is independently shippable. Sequence is recommended but not strict — R1, R4, R6, R7 are all safe to land in any order.

### Unit 1 — Drop HTTPS remotes; only export SSH-shaped origins (R1)

**File:** `.claude/hooks/otel_span.py:53-67`

Rather than try to redact credentials out of HTTPS URLs, refuse to export any origin that isn't SSH-shaped. SSH remotes (`git@host:org/repo.git`) carry no embedded credentials — only an SSH login name, which is not sensitive. HTTPS remotes are the credential-leak vector (`https://user:token@host/...`), and the only safe action on a non-SSH remote is to drop the attribute entirely.

Behavior change:

- If `origin` matches the SSH shape (`<user>@<host>:<path>`), set `git.origin` and derive `git.repo` from it.
- Otherwise (HTTPS, git://, file://, anything else), skip both attributes. `git.repo` stays unset rather than risking a partial leak.

Detection: an SSH remote contains `@` before any `/` and has a `:` after the `@` separating host from path. A simple check is enough:

```python
def _is_ssh_remote(url: str) -> bool:
    if "://" in url:           # rules out https://, git://, ssh://
        return False
    at = url.find("@")
    colon = url.find(":")
    return 0 < at < colon
```

Acceptance:
- With a real SSH origin (`git@github.com:org/repo.git`), span carries `git.origin` and `git.repo`.
- With `https://user:token@example.com/x/y.git`, span carries neither attribute. Smoke-test: set that origin in a throwaway repo, run `python3 -c "from otel_span import get_git_context; print(get_git_context())"`, confirm the returned dict is empty.
- Document the behavior change in the README under "What this captures" — HTTPS-remote repos will show empty `git.*` attributes by design.

### Unit 2 — Wrap unprotected tempfile writes (R3)

**Files:**
- `.claude/hooks/hook_pre_tool_use.py:23-24`
- `.claude/hooks/hook_user_prompt_submit.py:23-24`
- `.claude/hooks/hook_permission_request.py:26-27`
- `.claude/hooks/hook_pre_compact.py:22-24`
- `.claude/hooks/hook_subagent_start.py:21-22`

Wrap each `open(..., "w")` write in `try/except OSError: pass`. The hook still emits its span (where applicable); only the coordination tempfile is skipped, which downgrades duration tracking but does not block the tool call.

Acceptance: simulate the failure with `chmod -w $TMPDIR` (or point `TMPDIR=/nonexistent`) and confirm the hook exits 0 with no traceback on stderr.

### Unit 3 — Symlink-safe tempfile creation (R2)

**File:** new module `otel_span.py` helper `_open_state_file(name) -> file`

Replace the five direct `open(path, "w")` callsites with a helper that:

1. Resolves a per-user state dir: `~/.cache/claude-hooks/` (create with `0o700` if absent).
2. Opens with `os.open(path, O_CREAT | O_WRONLY | O_TRUNC, 0o600)` so existing symlinks aren't followed (`O_NOFOLLOW` on Linux; on macOS the equivalent is `O_NOFOLLOW_ANY`/`O_NOFOLLOW`).
3. Returns a file object via `os.fdopen(fd, "w")`.

Update all five readers (PostToolUse, PostToolUseFailure, PostCompact, PermissionDenied, SubagentStop) to read from the new path. Also update the README's "Tempfile leaks" note to reflect the new location.

Acceptance: pre-create a symlink at the expected path pointing at `/tmp/canary`; run the hook; confirm `/tmp/canary` is unmodified and the hook still works (or fails closed if `O_NOFOLLOW` blocks it — both are acceptable, crashing the user's file is not).

### Unit 4 — Remove no-op `os.path.expanduser` (R4)

**Files:** all 18 hook scripts, line ~11.

Replace:
```python
sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))
```
with:
```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

Verify with `grep -n "expanduser" .claude/hooks/*.py` returning no matches afterwards.

Acceptance: run `python3 -c "import sys; sys.path.insert(0, '/tmp/x'); import otel_span"` — no behavior change; hooks still locate `otel_span`.

### Unit 5 — Quarantine `hook_test_env.py` (R5)

**File:** `.claude/hooks/hook_test_env.py`

Two-part fix:

1. **Redact credential-shaped env vars** before printing. Mask any key matching `(?i)(key|token|secret|password|headers|credential|auth)` — replace value with `<redacted, N chars>`.
2. **Move out of the active hooks dir.** Either rename to `_dev_dump_env.py` (leading underscore signals "not a real hook") or move to `.claude/hooks/dev/dump_env.py`. Update any references.

Acceptance: `python3 hook_test_env.py < /dev/null` no longer prints anything resembling `x-honeycomb-team=...real-key-prefix...`.

### Unit 6 — Log JSON parse failures to stderr (R6)

**File:** `.claude/hooks/otel_span.py:135-140`

In `read_stdin`, catch `JSONDecodeError`, print one line to stderr (`[otel_span] stdin was not valid JSON; emitting empty-attrs span`), then `return {}`. Behavior preserved; signal added.

Acceptance: `echo 'not json' | python3 hook_session_start.py` writes one line to stderr and exits 0.

### Unit 7 — Gitignore `__pycache__/` in hooks dir (R7)

**File:** new `.claude/hooks/.gitignore` (or extend a higher-level gitignore).

Add:
```
__pycache__/
*.pyc
```

If `__pycache__/` is already tracked, `git rm -r --cached .claude/hooks/__pycache__` first.

Acceptance: `git check-ignore .claude/hooks/__pycache__` exits 0.

## Out of Scope (Tracked Separately)

These came up in the review but don't belong in this plan:

- **OTLP synchronous-flush latency** (P1 #3): 100-300ms per tool call, up to 3s on backend stall. Mitigation requires either `BatchSpanProcessor` + a daemon, or `os.fork()` fire-and-forget. Design decision, not a bug fix — capture in a separate plan if pursuing.
- **Tempfile-coordination correctness when `tool_use_id` is missing** (P1 #5): genuinely Claude Code's contract to honor; verify by running with `OTEL_LOG_TOOL_DETAILS=1` for a week and grepping for empty `tool_use_id` in Honeycomb before changing anything.
- **`OTEL_SERVICE_NAME` alignment with Claude Code's built-in telemetry service name** (P2 #10): cosmetic; pick a name once and document it.
- **Stop-hook `status_ok` enum mismatch** (P2 #11): verify the actual Claude Code event schema before changing the predicate.
- **Honeycomb API key + Slack bot token in plaintext in `~/.claude/settings.json`**: credential-hygiene issue for the *consumer* of these hooks, not the hooks themselves. Rotate the keys; consider a keychain helper. Tracked in session memory observation #826.

## Verification

After each unit lands:

```bash
# Smoke test that all hooks still import cleanly
for f in .claude/hooks/hook_*.py; do
  python3 -c "import ast; ast.parse(open('$f').read())" || echo "PARSE FAIL: $f"
done

# Smoke test that emit_span still works end-to-end
echo '{"session_id":"plan-test","cwd":"/tmp","trigger":"startup"}' | python3 .claude/hooks/hook_session_start.py
```

After Unit 1 specifically, confirm Honeycomb receives a span with `git.origin` set and **no userinfo prefix** by running against a repo whose origin you've temporarily munged with embedded credentials.

## Notes

- This plan is intentionally bias-toward-shipping. Each unit is small (< 30 lines changed for most), independently testable, and reversible with a single revert. No unit blocks any other.
- Recommended landing order if doing one PR per unit: 4 → 7 → 6 → 1 → 2 → 3 → 5. (Trivial cleanups first to clear noise; credential redaction next because it has the largest blast radius; symlink-safety and `hook_test_env` last because they restructure files.)
