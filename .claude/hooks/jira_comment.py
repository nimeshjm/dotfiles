#!/usr/bin/env python3
"""
jira_comment.py — post a turn summary to the Jira ticket named in the git branch.

Called by hook_stop.py after span emission. Branch names follow the
{PROJECT}-{NUMBER}-{description} convention (e.g. CSMP-1234-fix-login), so the
ticket ID is extracted from the branch and a Markdown comment summarising the
turn (request, summary, tools, code changes, plan) is posted via the jira CLI.
Everything fails soft: any error is logged and the hook continues.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import time

from otel_span import log_debug
from transcript import read_turn_data

# Jira Cloud rejects comments longer than 32,767 characters
JIRA_COMMENT_LIMIT = 32_000


def extract_branch_ticket(cwd: str) -> str:
    """Return Jira ticket ID from the active git branch, e.g. 'CSMP-1234'."""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd or None,
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return ""
        m = re.search(r'\b([A-Z]+-\d+)\b', r.stdout.strip())
        return m.group(1) if m else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        log_debug(f"git branch error: {e}")
        return ""


def find_recent_plan(cwd: str = "", tool_calls: list | None = None) -> str:
    """Return content of the plan file for this session.

    Priority:
    1. A plan file written/edited by a tool call in the current turn.
    2. A plan file whose name starts with the cwd-derived slug prefix (written within 7 days).
    3. The most recently modified plan within 24h (mtime fallback).
    """
    plans_dir = os.path.expanduser("~/.claude/plans")

    # 1. Tool-call evidence: most reliable — this turn explicitly wrote the plan
    if tool_calls:
        for tc in tool_calls:
            if tc["name"] in ("Write", "Edit"):
                path = tc["input"].get("file_path", "")
                if path.startswith(plans_dir) and path.endswith(".md"):
                    try:
                        with open(path) as f:
                            return f.read()
                    except OSError:
                        pass

    # 2. cwd slug prefix match — covers turns that read a plan created earlier this session
    if cwd:
        cwd_slug = cwd.lower().replace("/", "-").lstrip("-")
        prefix = cwd_slug[:35]
        try:
            candidates = [
                f for f in glob.glob(os.path.join(plans_dir, "*.md"))
                if os.path.basename(f).startswith(prefix)
            ]
            if candidates:
                candidates.sort(key=os.path.getmtime, reverse=True)
                if time.time() - os.path.getmtime(candidates[0]) < 86400 * 7:
                    with open(candidates[0]) as f:
                        return f.read()
        except OSError:
            pass

    # 3. Mtime fallback — last resort; may return a plan from a different session
    try:
        files = glob.glob(os.path.join(plans_dir, "*.md"))
        if not files:
            return ""
        files.sort(key=os.path.getmtime, reverse=True)
        if time.time() - os.path.getmtime(files[0]) > 86400:
            return ""
        with open(files[0]) as f:
            return f.read()
    except OSError:
        return ""


def format_comment(turn: dict, plan_content: str) -> str:
    """Build a Markdown comment summarising the turn."""
    user_prompt   = turn["user_prompt"]
    final_summary = turn["final_summary"]
    tool_calls    = turn["tool_calls"]
    loc_changes   = turn["loc_changes"]

    parts = ["## Claude Code Turn Summary\n"]

    if user_prompt:
        parts.append(f"**Request:** {user_prompt}\n")

    if final_summary:
        parts.append("### Summary\n")
        parts.append(f"{final_summary}\n")

    if tool_calls:
        parts.append("### Tools Called\n")
        counts: dict = {}
        for tc in tool_calls:
            counts[tc["name"]] = counts.get(tc["name"], 0) + 1
        for name, n in sorted(counts.items(), key=lambda x: -x[1]):
            parts.append(f"- {name} ({n}x)")
        parts.append("")

    if loc_changes:
        parts.append("### Code Changes\n")
        home = os.path.expanduser("~")
        for path, ch in loc_changes.items():
            short = path.replace(home, "~")
            parts.append(f"- `{short}`: +{ch['added']} / -{ch['removed']} lines")
        parts.append("")

    if plan_content:
        parts.append("---\n### Plan\n")
        parts.append(f"```\n{plan_content}\n```")

    comment = "\n".join(parts)
    if len(comment) > JIRA_COMMENT_LIMIT:
        marker = "\n```\n\n_[comment truncated to fit Jira's 32,767-character limit]_"
        comment = comment[:JIRA_COMMENT_LIMIT] + marker
    return comment


def post_comment(ticket_id: str, comment: str) -> bool:
    """Post comment to a Jira ticket via the jira CLI."""
    try:
        r = subprocess.run(
            ["jira", "issue", "comment", "add", ticket_id, comment],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            log_debug(f"jira comment posted to {ticket_id}")
            return True
        log_debug(f"jira CLI error ({r.returncode}): {r.stderr[:200]}")
        return False
    except (OSError, subprocess.TimeoutExpired) as e:
        log_debug(f"jira comment error: {e}")
        return False


def post_turn_summary(cwd: str, transcript_path: str) -> None:
    """Extract ticket → read turn data → find plan → format → post.

    No-op (logged) when the branch carries no ticket ID.
    """
    ticket_id = extract_branch_ticket(cwd)
    if not ticket_id:
        log_debug("no jira ticket in branch, skipping comment")
        return
    log_debug(f"posting jira comment to {ticket_id}")
    turn = read_turn_data(transcript_path)
    plan_content = find_recent_plan(cwd=cwd, tool_calls=turn["tool_calls"])
    post_comment(ticket_id, format_comment(turn, plan_content))
