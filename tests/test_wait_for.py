"""Tests for watcher/wait_for.py."""
import os
import subprocess
import sys
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "watcher", "wait_for.py")


def run(*args, timeout=30):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=timeout)


def test_passes_when_the_check_succeeds_later(tmp_path):
    flag = tmp_path / "done"
    subprocess.Popen(["bash", "-c", f"sleep 1.5; touch {flag}"])
    t = time.monotonic()
    r = run("--every", "0.5", "--timeout", "10", "--", f"test -e {flag} && echo ready")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ready" in r.stdout and "check passed" in r.stdout
    assert time.monotonic() - t < 8


def test_times_out_with_124_and_prints_the_last_output():
    r = run("--every", "0.3", "--timeout", "1", "--", "echo not-yet; exit 3")
    assert r.returncode == 124
    assert "not-yet" in r.stdout and "exit 3" in r.stdout


def test_refuses_bad_arguments():
    assert run("echo hi").returncode == 2
    assert run("--").returncode == 2
    assert run("--timeout", "541", "--", "true").returncode == 2
    assert run("--every", "nan", "--", "true").returncode == 2
    assert run("--every", "0", "--", "true").returncode == 2
    assert run("--bogus", "1", "--", "true").returncode == 2


def test_argv_form_runs_without_a_shell(tmp_path):
    r = run("--timeout", "5", "--", "test", "-d", str(tmp_path))
    assert r.returncode == 0


def test_checks_again_in_the_final_partial_interval(tmp_path):
    # checks at 0 and 2 s fail, the last check at 2.5 s (half an interval before the timeout) sees the flag
    flag = tmp_path / "late"
    subprocess.Popen(["bash", "-c", f"sleep 2.2; touch {flag}"])
    r = run("--every", "2", "--timeout", "3.5", "--", f"test -e {flag}")
    assert r.returncode == 0, r.stdout + r.stderr


def test_timeout_shorter_than_the_interval_checks_until_the_deadline():
    # Sol's case: the check turns true at 0.75 s of a 1 s timeout, with a 2 s interval
    at = time.time() + 0.75
    r = run("--every", "2", "--timeout", "1", "--",
            f"{sys.executable} -c 'import sys, time; sys.exit(time.time() < {at})'")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_false_check_uses_the_whole_timeout():
    for every, timeout in (("2", "1"), ("0.4", "1.5")):
        t = time.monotonic()
        r = run("--every", every, "--timeout", timeout, "--", "false")
        took = time.monotonic() - t
        assert r.returncode == 124
        assert float(timeout) - 0.15 < took < float(timeout) + 0.5, took


def test_stopping_wait_for_kills_the_running_check():
    marker = "41.75"
    p = subprocess.Popen([sys.executable, SCRIPT, "--timeout", "30", "--", f"sleep {marker}; false"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.5)
    p.terminate()
    assert p.wait(timeout=5) == 130
    time.sleep(0.2)
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.splitlines()
    assert f"sleep {marker}" not in [x.strip() for x in ps]


def test_a_background_child_does_not_hold_a_passed_check():
    t = time.monotonic()
    r = run("--timeout", "5", "--", "sleep 3.25 & echo started")
    assert r.returncode == 0 and "started" in r.stdout
    assert time.monotonic() - t < 2
    subprocess.run(["pkill", "-f", "^sleep 3.25$"])


def test_a_success_after_the_timeout_is_a_timeout():
    t = time.monotonic()
    r = run("--timeout", ".5", "--", "sleep 1; true")
    assert r.returncode == 124, r.stdout
    assert time.monotonic() - t < 0.95


def test_a_check_past_the_timeout_is_killed_with_its_children():
    marker = "37.25"
    r = run("--timeout", "1", "--", f"sleep {marker}; true")
    assert r.returncode == 124
    time.sleep(0.2)
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.splitlines()
    assert f"sleep {marker}" not in [p.strip() for p in ps]
