#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))

# Print all environment variables to stdout
print("=" * 80)
print("ENVIRONMENT VARIABLES IN HOOK")
print("=" * 80)

env_vars = sorted(os.environ.items())
for key, value in env_vars:
    # Truncate long values for readability
    if len(value) > 100:
        value_display = value[:100] + "..."
    else:
        value_display = value
    print(f"{key}={value_display}")

print("=" * 80)
print(f"Total env vars: {len(env_vars)}")
print("=" * 80)
