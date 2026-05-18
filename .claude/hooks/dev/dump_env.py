#!/usr/bin/env python3
"""Dev tool: dump hook environment variables. NOT a configured hook.

Run manually to inspect the subprocess environment:
    echo '{}' | python3 .claude/hooks/dev/dump_env.py

Credential-shaped values are redacted before printing.
"""
import re
import os

_REDACT_RE = re.compile(r"(?i)(key|token|secret|password|headers|credential|auth)")

env_vars = sorted(os.environ.items())
print("=" * 80)
print("ENVIRONMENT VARIABLES IN HOOK SUBPROCESS")
print("=" * 80)
for key, value in env_vars:
    if _REDACT_RE.search(key):
        display = f"<redacted, {len(value)} chars>"
    elif len(value) > 100:
        display = value[:100] + "..."
    else:
        display = value
    print(f"{key}={display}")
print("=" * 80)
print(f"Total env vars: {len(env_vars)}")
print("=" * 80)
