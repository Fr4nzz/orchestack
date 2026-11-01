#!/usr/bin/env python3
"""orchestack memory gate: a Claude Code PreToolUse hook for Agent, delegate_task and t3_thread_launch.

Gates on memory only, never on the number of agents. If available memory is below the threshold, it prints a
PreToolUse deny decision with the reason. Otherwise it prints nothing (the call proceeds as usual).

available = MemAvailable + swap_weight * SwapFree     (swap_weight 0 by default)

Config: config.json in $ORCHESTACK_CONFIG_DIR or ~/.config/orchestack:
  {"memory_gate": {"min_available_gib": 3.0, "swap_weight": 0.0, "enabled": true}}
Calls that run on another machine (T3 environmentId, Agent isolation "remote") are not gated.
Tests may point $ORCHESTACK_MEMINFO at a fake /proc/meminfo. Fails open: any error allows the call.
"""
import json
import os
import sys

DEFAULTS = {"min_available_gib": 3.0, "swap_weight": 0.0, "enabled": True}
GIB_KB = 1024 * 1024


def config():
    """Defaults when config.json is missing; None (allow the call) when it exists but cannot be read or parsed."""
    cdir = os.path.expanduser(os.environ.get("ORCHESTACK_CONFIG_DIR") or "~/.config/orchestack")
    path = os.path.join(cdir, "config.json")
    cfg = dict(DEFAULTS)
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, encoding="utf-8") as f:
            section = json.load(f).get("memory_gate", {}) or {}
        if not isinstance(section, dict):
            return None
        cfg.update(section)
        float(cfg["min_available_gib"]), float(cfg["swap_weight"])
    except Exception:
        return None
    return cfg


def meminfo(path):
    values = {}
    with open(path, encoding="ascii") as f:
        for line in f:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                values[key.strip()] = int(parts[0])  # kB
    return values


def remote_call(tool_name, tool_input):
    if not isinstance(tool_input, dict):
        return False
    if tool_input.get("environmentId"):
        return True
    return tool_name == "Agent" and tool_input.get("isolation") == "remote"


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except Exception:
        event = {}
    cfg = config()
    if cfg is None or not cfg.get("enabled", True):
        return
    if remote_call(event.get("tool_name", ""), event.get("tool_input")):
        return
    mem = meminfo(os.environ.get("ORCHESTACK_MEMINFO") or "/proc/meminfo")
    avail_kb = mem["MemAvailable"] + float(cfg.get("swap_weight", 0.0)) * mem.get("SwapFree", 0)
    avail = avail_kb / GIB_KB
    need = float(cfg.get("min_available_gib", 3.0))
    if avail >= need:
        return
    reason = (
        f"orchestack memory gate: free RAM {avail:.1f} GiB < {need:.1f} GiB. "
        "Retry after the next child completion notice or once memory is freed; do not sleep-poll. "
        "(Threshold: memory_gate.min_available_gib in ~/.config/orchestack/config.json.)"
    )
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
