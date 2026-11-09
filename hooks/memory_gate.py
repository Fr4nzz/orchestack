#!/usr/bin/env python3
"""orchestack memory gate: a Claude Code PreToolUse hook for Agent, delegate_task and t3_thread_launch.

Gates on memory only, never on the number of agents. If available memory is below the threshold, it prints a
PreToolUse deny decision with the reason. Otherwise it prints nothing (the call proceeds as usual).

available = MemAvailable + swap_weight * SwapFree     (swap_weight 0 by default)

Before denying it reads memory twice more over recheck_s seconds (default 3), since memory just under the limit often
frees within seconds, and names the largest processes by their command. The whole hook keeps to a 3.5 s deadline
(the hook timeout is 5 s): the re-check sleeps are capped to it and the process scan stops at it.

Config: config.json in $ORCHESTACK_CONFIG_DIR or ~/.config/orchestack:
  {"memory_gate": {"min_available_gib": 3.0, "swap_weight": 0.0, "recheck_s": 3.0, "enabled": true}}
Calls that run on another machine (T3 environmentId, Agent isolation "remote") are not gated.
Tests may point $ORCHESTACK_MEMINFO at a fake /proc/meminfo. Fails open: any error allows the call.
"""
import json
import os
import sys
import time

DEFAULTS = {"min_available_gib": 3.0, "swap_weight": 0.0, "recheck_s": 3.0, "enabled": True}
GIB_KB = 1024 * 1024
DEADLINE_S = 3.5  # for the whole hook, below the 5 s hook timeout
SCAN_RESERVE_S = 0.5  # of that, kept for the process scan


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


def available(cfg):
    mem = meminfo(os.environ.get("ORCHESTACK_MEMINFO") or "/proc/meminfo")
    return (mem["MemAvailable"] + float(cfg.get("swap_weight", 0.0)) * mem.get("SwapFree", 0)) / GIB_KB


def process_name(pid):
    """The command of a process: the basename of argv[0] from /proc/PID/cmdline, else its comm.

    Not the Name field of /proc/PID/status, which is the main thread's name (MainThread for many Node, Python
    and browser processes)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv0 = f.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")
    except OSError:
        argv0 = ""
    if argv0:
        if not os.path.exists(argv0):
            argv0 = argv0.split(" ", 1)[0].rstrip(":")  # a title the process set, e.g. "sshd: user@pts/0"
        name = os.path.basename(argv0)
        if name:
            return name
    with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
        return f.read().strip() or "?"


def process_rss_kb(pid):
    with open(f"/proc/{pid}/statm", encoding="ascii") as f:
        return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)


def largest_processes(n, deadline=None):
    """'node 1.6 GiB (pid 1234), chromium 2.1 GiB (14 processes), ...' for the n commands with the largest total
    resident memory, from /proc. Stops reading at the monotonic deadline. Empty on any error."""
    by_name = {}
    try:
        for pid in os.listdir("/proc"):
            if deadline is not None and time.monotonic() >= deadline:
                break
            if not pid.isdigit():
                continue
            try:
                rss = process_rss_kb(pid)
                if rss <= 0:
                    continue
                name = process_name(pid)
            except (OSError, ValueError, IndexError):
                continue
            total, count, top_pid, top_rss = by_name.get(name, (0, 0, pid, 0))
            if rss > top_rss:
                top_pid, top_rss = pid, rss
            by_name[name] = (total + rss, count + 1, top_pid, top_rss)
    except OSError:
        return ""
    ranked = sorted(by_name.items(), key=lambda kv: kv[1][0], reverse=True)[:n]
    return ", ".join(f"{name} {total / GIB_KB:.1f} GiB " + (f"(pid {pid})" if count == 1 else f"({count} processes)")
                     for name, (total, count, pid, _) in ranked)


def remote_call(tool_name, tool_input):
    if not isinstance(tool_input, dict):
        return False
    if tool_input.get("environmentId"):
        return True
    return tool_name == "Agent" and tool_input.get("isolation") == "remote"


def main():
    deadline = time.monotonic() + DEADLINE_S
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except Exception:
        event = {}
    cfg = config()
    if cfg is None or not cfg.get("enabled", True):
        return
    if remote_call(event.get("tool_name", ""), event.get("tool_input")):
        return
    need = float(cfg.get("min_available_gib", 3.0))
    try:
        recheck = float(os.environ.get("ORCHESTACK_GATE_RECHECK_S") or cfg.get("recheck_s", 3.0))
    except (TypeError, ValueError):
        recheck = 3.0
    recheck = min(max(recheck, 0.0), DEADLINE_S - SCAN_RESERVE_S)
    avail = available(cfg)
    for _ in range(2):  # memory just under the limit often frees within seconds: read twice more before denying
        if avail >= need:
            return
        time.sleep(max(0.0, min(recheck / 2, deadline - SCAN_RESERVE_S - time.monotonic())))
        avail = available(cfg)
    if avail >= need:
        return
    top = largest_processes(3, deadline)
    reason = (
        f"orchestack memory gate: free RAM {avail:.1f} GiB < {need:.1f} GiB. "
        + (f"Largest processes: {top}. Stop your own leftover servers or browsers if they are among them. " if top else "")
        + "Retry after the next child completion notice or once memory is freed. Do not sleep-poll. "
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
