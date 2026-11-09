#!/usr/bin/env python3
"""wait_for: wait inside one tool call until a check command succeeds, instead of `sleep N` and then a check.

    python3 wait_for.py [--every SECONDS] [--timeout SECONDS] -- <check command...>

Runs the check command at once and then every --every seconds (default 30) until it exits 0, or until --timeout
seconds have passed (default 420, at most 540, so the call ends before a 600 s foreground tool limit). Checks go
on until the timeout itself: when less than an interval is left, the wait halves each time (not below 1 s, or a
tenth of the interval if shorter), and each check gets only the time that is left. A check still running at the
timeout is killed with everything it started, and a success after the timeout counts as a timeout. If wait_for
itself is stopped (SIGTERM, SIGINT), it kills the running check first. The check runs through `bash -c` when given
as one argument, so pipes and `&&` work. Prints the check's last output.

Exit codes: 0 the check succeeded before the timeout, 124 the timeout passed first (the last check's output is
printed), 2 bad arguments. A check that keeps failing is not an error of this tool: read its output.

Example: python3 wait_for.py --every 30 --timeout 420 -- 'test -s results/metrics.json'
"""
import math
import os
import signal
import subprocess
import sys
import tempfile
import time

MAX_TIMEOUT = 540
CURRENT = None  # the running check, killed if wait_for itself is stopped


def bad(msg):
    print(f"wait_for: {msg}", file=sys.stderr)
    sys.exit(2)


def parse(argv):
    every, timeout = 30.0, 420.0
    if "--" not in argv:
        bad("usage: wait_for.py [--every S] [--timeout S] -- <check command>")
    i = argv.index("--")
    opts, cmd = argv[:i], argv[i + 1:]
    if not cmd:
        bad("give a check command after --")
    j = 0
    while j < len(opts):
        if opts[j] in ("--every", "--timeout") and j + 1 < len(opts):
            try:
                v = float(opts[j + 1])
            except ValueError:
                bad(f"{opts[j]} needs a number of seconds")
            if not math.isfinite(v) or v <= 0:
                bad(f"{opts[j]} must be a positive number of seconds")
            if opts[j] == "--every":
                every = v
            else:
                timeout = v
            j += 2
        else:
            bad(f"unknown option {opts[j]}")
    if timeout > MAX_TIMEOUT:
        bad(f"--timeout above {MAX_TIMEOUT} s would outlive a 600 s tool call. Use a background wait or the watcher")
    return every, timeout, cmd


def kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def run(cmd, limit):
    """(exit code, output) of one check that may run for at most `limit` seconds. The check runs in its own process
    group, killed whole on timeout, so a shell does not leave a hung ssh or sleep behind. Output goes to a file, so
    a background child that keeps stdout open cannot hold the check past its exit."""
    global CURRENT
    args = ["bash", "-c", cmd[0]] if len(cmd) == 1 else cmd
    with tempfile.TemporaryFile("w+") as out:
        try:
            p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True)
        except OSError as e:
            return 127, str(e)
        CURRENT = p
        try:
            rc = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            kill(p)
            p.wait()
            rc = None
        finally:
            CURRENT = None
        out.seek(0)
        text = out.read()[-20000:].strip()
    if rc is None:
        return 124, (text + "\n" if text else "") + "(the check itself ran past the timeout and was stopped)"
    return rc, text


def stop(signum, _frame):
    if CURRENT is not None:
        kill(CURRENT)
    print(f"wait_for: stopped by signal {signum}", file=sys.stderr)
    sys.exit(130)


def main(argv):
    every, timeout, cmd = parse(argv)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    start = time.monotonic()
    deadline = start + timeout
    floor = min(1.0, every / 10)
    tries, rc, out, last = 0, 124, "", False
    while True:
        began = time.monotonic()
        if began >= deadline:
            break
        rc, out = run(cmd, deadline - began)
        tries += 1
        end = time.monotonic()
        if rc == 0 and end <= deadline:
            if out:
                print(out[-4000:])
            print(f"wait_for: check passed after {end - start:.1f} s ({tries} checks)")
            return 0
        if rc == 0:
            rc, out = 124, (out + "\n" if out else "") + "(the check passed only after the timeout)"
        left = deadline - end
        if left <= 0 or last:
            break
        if left <= floor:
            last = True          # one more check now, with the time that is left
            continue
        time.sleep(min(every, max(left / 2, floor)))   # in the tail the wait halves, so checks reach the timeout
    if out:
        print(out[-4000:])
    print(f"wait_for: check still failing (exit {rc}) after {time.monotonic() - start:.1f} s ({tries} checks)")
    return 124


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
