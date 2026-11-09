"""Tests for retro/scan.py: clustering, owner split, fine cases, redaction, week-over-week change, Codex sessions.

Run: python3 -m pytest tests   (or: uvx pytest tests)
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "retro" / "scan.py"
NOW = 1_800_000_000.0                     # 2027-01-15T08:00Z
TS = "2027-01-14T12:00:00.000Z"           # inside a 7-day window
OLD = "2027-01-01T12:00:00.000Z"          # outside it

sys.path.insert(0, str(REPO / "retro"))
import scan  # noqa: E402


class Transcripts:
    """A fake Claude config dir (<tmp>/.claude-acct/projects/<slug>/<id>.jsonl) and a writer for tool calls."""

    def __init__(self, tmp, account=".claude-acct"):
        self.root = tmp / account / "projects"
        self.n = 0

    def session(self, sid, cwd, calls, slug="-proj"):
        d = self.root / slug
        d.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "api-request", "timestamp": TS, "message": {"content": [{"type": "tool_use", "id": "dup", "name": "Bash"}]}}]
        for name, inp, result, is_error, *ts in calls:
            self.n += 1
            uid = f"t{self.n}"
            stamp = ts[0] if ts else TS
            lines.append({"type": "assistant", "cwd": cwd, "sessionId": sid, "timestamp": stamp,
                          "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}})
            lines.append({"type": "user", "cwd": cwd, "sessionId": sid, "timestamp": stamp,
                          "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": result,
                                                   "is_error": is_error}]}})
        f = d / f"{sid}.jsonl"
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(f, (NOW, NOW))


def run(tmp, *extra, config=None, date="2027-01-15", now=NOW):
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"retro": config or {}}))
    out = tmp / "out"
    p = subprocess.run([sys.executable, "-I", str(SCAN), "--config", str(cfg), "--out", str(out), "--date", date,
                        "--now", str(now), "--claude-root", str(tmp / ".claude-acct" / "projects"),
                        "--codex-root", str(tmp / "codex"), *extra], capture_output=True, text=True, check=True)
    md = (out / f"{date}.md").read_text()
    js = json.loads((out / f"{date}.json").read_text())
    return p, md, js


APP = {"owners": [{"name": "app assistant", "cwd": [r"/\.myapp-lab/workspaces"], "own_permissions": True}],
       "pc_tools": ["^mcp__shared__"]}


def test_clusters_owner_split_and_fine_cases(tmp_path):
    repo = tmp_path / "src" / "webshop"
    (repo / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    t = Transcripts(tmp_path)
    t.session("s-app", str(tmp_path / ".myapp-lab" / "workspaces" / "lab"), [
        ("mcp__db__query", {}, '{"error":"SQLite: no such column: Wild_Reared"}', True),
        ("mcp__db__query", {}, '{"error":"SQLite: no such column: Date"}', True),
        ("mcp__db__query", {}, "ok", False),
        ("Read", {}, "Claude requested permissions to read from /x, but you haven't granted it yet.", True),
        ("Bash", {"command": "python3 -c 'import numpy'"},
         "Exit code 1\nTraceback (most recent call last):\nModuleNotFoundError: No module named 'numpy'", True),
    ])
    t.session("s-web", str(repo / "sub"), [
        ("Bash", {"command": "ls nothere"}, "Exit code 2\nls: cannot access 'nothere': No such file or directory", True),
        ("Bash", {"command": "grep -r foo ."}, "Exit code 1", True),
        ("Bash", {"command": "grep -c foo x | head"}, "Exit code 1\n0", True),
        ("Bash", {"command": "rg foo src"}, "Exit code 1\nsrc/a.py-1-bar", True),
        ("Bash", {"command": "rm -rf build"}, "The user doesn't want to proceed with this tool use. The tool use was rejected", True),
        ("Bash", {"command": "make"}, "<tool_use_error>Cancelled: parallel tool call Bash(ls) errored</tool_use_error>", True),
        ("Read", {}, "The tool call was interrupted before a result was received.", True),
        ("Bash", {"command": "jq ."}, "Exit code 127\nbash: jq: command not found", True),
        ("Bash", {"command": "sleep 60; cat x"}, "<tool_use_error>Blocked: sleep 60 followed by: cat x.</tool_use_error>", True),
        ("Bash", {"command": "sleep 1"}, "Exit code 1\nold error", True, OLD),
        ("mcp__shared__send", {}, '{"_tag":"Failure","code":"thread_not_found","message":"gone"}', True),
        ("Read", {}, "File does not exist.", True),
        # kept: a silent failure of a command that is no search or test, a test runner after `test`, and an error
        # after a long run of whitespace from grep
        ("Bash", {"command": "python check.py"}, "Exit code 1\n", True),
        ("Bash", {"command": "test -f setup.py && pytest -q"}, "Exit code 1\n1 failed\nFAILED t.py::x - assert 1 == 2", True),
        ("Bash", {"command": "grep x f"}, "Exit code 1\n" + " " * 300 + "\ngrep: f: No such file or directory", True),
        # kept: a silent failure after a search or test that succeeded, or at the end of a pipeline
        ("Bash", {"command": 'test -d / && python3 -c "import sys;sys.exit(1)"'}, "Exit code 1", True),
        ("Bash", {"command": "grep -q needle file && python check.py"}, "Exit code 1\n", True),
        ("Bash", {"command": "rg -l foo | python parse.py"}, "Exit code 1", True),
    ])
    p, md, js = run(tmp_path, config=APP)
    assert "errors=" in p.stdout and "no such column" not in p.stdout   # stdout carries totals, not error text
    cl = js["clusters"]
    assert cl["app assistant\tno such column\tmcp db/query"] == 2
    assert cl["app assistant\tblocked by a permission rule\tRead"] == 1      # own_permissions keeps it
    assert cl["this PC\tpython module missing\tBash"] == 1                   # interpreter errors go to this PC
    assert "project webshop\tfile or path missing\tRead" in cl                # git root of a subdirectory
    assert cl["project webshop\tfile or path missing\tRead"] == 1
    assert cl["this PC\tcommand not found\tBash"] == 1
    assert cl["this PC\tblocked: foreground sleep\tBash"] == 1
    assert cl["this PC\tMCP thread_not_found\tmcp shared/send"] == 1        # pc_tools
    # python check.py alone, the three compound commands above, and `grep -c foo x | head` (compound too)
    assert cl["project webshop\tnon-zero exit (other)\tBash"] == 5
    assert cl["project webshop\ttest or check failed\tBash"] == 1             # pytest after test -f
    assert cl["project webshop\tfile or path missing\tBash"] == 2             # ls, and grep's error after spaces
    assert js["errors"] == 16
    assert js["fine"] == {"exit 1 with no output from grep, rg, diff, cmp, test or pgrep": 1,
                          "exit 1 from grep, rg, diff, cmp, test or pgrep": 1,
                          "user rejected the call": 1, "cancelled after a sibling call failed": 1, "interrupted": 1}
    assert "old error" not in md                         # outside the window
    assert js["calls"] == 22                             # the api-request lines are skipped, the OLD call too
    # this PC lists where its errors came from, and its section comes first
    assert md.index("## this PC") < md.index("## app assistant")
    assert "### python module missing, Bash: 1. From: app assistant 1." in md   # no baseline: no change column
    assert "so there is no change column" in md
    assert "s-app" in md and ".jsonl:" in md


def test_worktree_counts_as_its_main_repo(tmp_path):
    main = tmp_path / "src" / "shop"
    (main / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    wt = tmp_path / "wt" / "shop-feature"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt1\n")
    o = scan.Owners([])
    assert o.of(str(wt)) == "project shop"
    assert o.of(str(tmp_path / "gone" / "shop-t3-6c905db9")) == "project shop"   # a removed T3 worktree


AT = "@"   # built at run time, so the repo's scrub check sees no address


def test_redaction_and_example_limits(tmp_path):
    secret_lines = [
        "export API_KEY=Zx8kQ2mN9pL4vR7tY1wE3sD6fG0hJ5cB",
        "git push https://bob:hunter22" + AT + "github.com/x.git",
        "curl -H 'Authorization: Bearer abc.def.ghi'",
        "password=hunter2 sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
        "mail me at someone" + AT + "example.org",
    ]
    t = Transcripts(tmp_path)
    for i in range(5):   # five sessions, one cluster: at most 3 examples
        t.session(f"s{i}", str(tmp_path / "p"), [
            ("Bash", {"command": "x"}, "Exit code 2\nfatal: " + " | ".join(secret_lines) + " " + "y" * 600, True)])
    _, md, js = run(tmp_path)
    for s in ("Zx8kQ2mN9pL4", "hunter22", "abc.def.ghi", "hunter2 ", "sk-ant-api03", "someone" + AT + "example.org"):
        assert s not in md
    assert js["clusters"]["project p\tgit error\tBash"] == 5
    sec = md.split("### git error, Bash")[1].split("###")[0]
    assert sec.count("\n- ") == 3
    assert "y" * 301 not in md                        # heads stop at 300 characters
    assert stat.S_IMODE(os.stat(tmp_path / "out" / "2027-01-15.md").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "out" / "2027-01-15.json").st_mode) == 0o600


def test_long_tokens_redacted_but_paths_kept():
    assert scan.redact("blob gAAAAABqvIXeiV2tIcm7xyI2CjStiPfPZlhs6qd5zH") == "blob <token>"
    path = "/x/MothSorter/run_explore_20260101/ft_RGBA_heldout.log"
    assert scan.redact(path) == path
    assert scan.redact("Original token count: 173") == "Original token count: 173"
    assert scan.redact("max_tokens: 3000, mkdir -p /tmp/x, git checkout -b fix") == \
        "max_tokens: 3000, mkdir -p /tmp/x, git checkout -b fix"
    assert scan.redact("Permission denied (publickey,password).") == "Permission denied (publickey,password)."
    assert scan.redact("PASS: created probe-1") == "PASS: created probe-1"          # test output, not a secret
    scratch = "S=/tmp/claude-1/-home-u-Documents-CodeProjs-MothSorter/run0042/run"
    assert scan.redact(scratch) == scratch                                          # a path with an assignment
    # base64 split by slashes, with long and short parts: the whole run goes
    assert scan.redact("k wJalrXUtnFEMI/K7MDENG/bPxRfiCYzzQ8rTt7Yy3Zz0Lm1Nn2Oo/k3") == "k <token>"


# Secret shapes the redaction must catch, built at run time so no scanner sees a token-like string in the repo.
HEX32 = "0123456789abcdef" * 2
B64 = "ab+cD3/P2vT3V0miK1wxqq5/Xy9zZ1Qw=="
SECRETS = {   # text in an error: the part that must not reach the digest
    "password=abc": "abc",
    "https://example.invalid/?token=abc&x=1": "abc",
    "--password hunter22": "hunter22",
    "Cookie: first=secretone; second=secrettwo": "secrettwo",
    "mysql -u root -pS3cretPw db": "S3cretPw",
    "sshpass -p hunter3 ssh x": "hunter3",
    "curl --token abcd1234efgh5678 x": "abcd1234efgh5678",
    "tsh login --token " + HEX32: HEX32,
    "api key: " + "f" * 32: "f" * 32,
    "--api-key=" + "e" * 32: "e" * 32,
    "BW_SESSION=" + B64: "P2vT3V0miK1wxqq5",
    "blob " + B64 + "/Q8rTt7Yy3Zz0Lm1Nn2Oo": "Q8rTt7Yy3Zz0Lm1Nn2Oo",
    "curl -H 'Authorization: Bearer abc.def.ghi'": "abc.def.ghi",
    "bearer " + "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0.abcdefgh": "eyJzdWIiOiIxIn0",
    "export GITHUB_TOKEN=" + "gh" + "p_" + "A1b2C3d4" * 5: "A1b2C3d4A1b2",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYzz": "wJalrXUtnFEMI",
    '{\\"password\\":\\"hun ter\\"}': "hun ter",
    '{"client_secret": "x1"}': "x1",
    "curl -u bob:pw1 https://x": "pw1",
    "git push https://" + "gh" + "p_" + "a" * 36 + AT + "github.com/x.git": "a" * 36,
    "postgres://u:dbpass" + AT + "db:5432/x": "dbpass",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE\n-----END OPENSSH PRIVATE KEY-----": "b3BlbnNzaC1rZXktdjE",
    "key " + "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuvwxyz0123": "abcdefghijklmnop",
    "key " + "sk" + "_live_" + "abcdefghijklmnop1234": "abcdefghijklmnop1234",
    "key " + "hf" + "_abcdefghijklmnopqrstuvwxyz": "abcdefghijklmnopqrstuvwxyz",
    "key " + "AK" + "IA" + "ABCDEFGHIJKLMNOP": "ABCDEFGHIJKLMNOP",
    "machine h login bob password netrcpw": "netrcpw",
    "commit " + HEX32 + "abcd1234": HEX32,
    # round 2: quoted values with escapes, long and unterminated quotes
    '{"password": "hun\\"ter99"}': "ter99",
    'sshpass -p "two word secret" ssh host': "word secret",
    'password="' + "z" * 201 + '"': "z" * 20,
    "passphrase='" + "correct horse battery staple " * 10 + "'": "battery",
    'token="unterminated secret value': "secret value",
    # whole headers: quoted cookies, every Digest field, in a shell argument or a JSON value
    'Cookie: a="secretone"; b=secrettwo': "secrettwo",
    'Authorization: Digest username="bob", response="shortresponse"': "shortresponse",
    "curl -H 'Authorization: Digest username=\"bob\", response=\"r1resp\"' https://x": "r1resp",
    'curl -H "Cookie: a=\\"q1q1\\"; b=q2q2" https://x': "q2q2",
    '{"Cookie": "a=b; c=jsoncookie"}': "jsoncookie",
    # -p flags of login commands, mysql with a quoted password
    "docker login -u me -p Xy9zAbCdEf123456 registry": "Xy9zAbCdEf123456",
    "podman login --password=pod12345 registry": "pod12345",
    "mysql -p'my pass' db": "my pass",
    # *_KEY, *_TOKEN, *_SECRET, *_SK and short names, space separators
    "MY_KEY=abcdef": "abcdef",
    "GEMINI_KEY=gem1ni": "gem1ni",
    "STRIPE_SK=strk": "strk",
    "BUILD_TOKEN_RO=tok3n": "tok3n",
    "APP_SECRET=appsecr": "appsecr",
    "pw=hunter5 PW=hunter6": "hunter",
    "apikey abcd1234": "abcd1234",
    "secret key: s3cr3tval": "s3cr3tval",
    "AWS_SECRET_ACCESS_KEY wJalrXUtnFEMIK7MDENGbPxRfiCYzz12": "wJalrXUtnFEMI",
    # long keys with one letter case, webhooks
    "using key " + "k3m9x2p7q1w8z4v6" * 2 + "a5b0c3d8": "k3m9x2p7q1w8",
    "NRAK-" + "X7Q2M9P4K1W8Z3V6B5N0C2D7Y": "X7Q2M9P4K1W8",
    "post https://hooks.slack.com/services/" + "T0001/B0001/" + "abcdefghijklmnopqrstuvwx": "abcdefghijklmnopqrstuvwx",
}


def test_every_secret_shape_is_redacted_in_the_digest(tmp_path):
    # one tool per secret, so each one is its own cluster and shown as an example in the rendered digest
    t = Transcripts(tmp_path)
    t.session("s0", str(tmp_path / "p"), [
        (f"Tool{i:02d}", {}, f"Error: failed\n{text}", True) for i, text in enumerate(SECRETS)])
    _, md, js = run(tmp_path)
    assert sum(js["clusters"].values()) == len(SECRETS)
    assert md.count("\n- acct ") + md.count("\n- .claude-acct ") == len(SECRETS)
    for text, secret in SECRETS.items():
        assert secret not in scan.redact(text), text
        assert secret not in scan.head(f"Error: failed\n{text}"), text
    for secret in SECRETS.values():
        assert secret not in md, secret
    # the redaction keeps what is not secret
    assert "Cookie: <redacted>" in md and "docker login -u me -p <redacted> registry" in md
    assert "curl -H 'Authorization: <redacted>' https://x" in md


def test_raw_cap_never_shows_part_of_a_cut_secret(tmp_path):
    # Sol's case: whitespace before the raw cap, a quoted password cut by the cap
    sol = " " * 23970 + 'password="' + "z" * 250 + '"'
    assert "zzz" not in scan.head(sol)
    # a hex run that shrinks to <hex>, then a quoted value with spaces cut by the cap
    shrink = "f" * (scan.RAW - 20) + ' password="abc def ghi jkl mno"'
    assert "def" not in scan.head(shrink) and "abc" not in scan.head(shrink)
    t = Transcripts(tmp_path)
    t.session("s0", str(tmp_path / "p"), [("ToolA", {}, sol, True), ("ToolB", {}, shrink, True)])
    _, md, _ = run(tmp_path)
    assert "zzz" not in md and "def ghi" not in md


def test_head_collapses_whitespace_and_drops_a_cut_secret():
    padded = "Exit code 1\n" + " " * 3000 + "\n" * 2000 + "token=" + "Zx8kQ2mN9pL4vR7tY1wE"
    assert "Zx8kQ2mN9pL4" not in scan.head(padded) and "token=<redacted>" in scan.head(padded)
    # a long hex run shrinks to <hex>, which would pull a token cut at the end of the prefix into view
    cut = "f" * 5990 + " " + "sk-" + "ant-a" + "bcdefghijklmnopqrstuvwxyz"
    assert "sk-" not in scan.head(cut)


TRACE = "Exit code 1\nTraceback (most recent call last):\n" + "".join(f'  File "/src/mod{i}.py", line {i}\n' for i in range(20))


def test_week_over_week_change(tmp_path):
    t = Transcripts(tmp_path)
    t.session("a", str(tmp_path / "p"), [
        ("Bash", {"command": "x"}, TRACE + "ValueError: bad", True),
        ("Bash", {"command": "y"}, "Exit code 1\nconnection refused", True),
    ])
    run(tmp_path, date="2027-01-08", now=NOW - 7 * 86400 + 3600)   # last week: files are newer than its window
    t.session("a", str(tmp_path / "p"), [
        ("Bash", {"command": "x"}, TRACE + "ValueError: bad", True),
        ("Bash", {"command": "x"}, TRACE + "ValueError: worse", True),
        ("Read", {}, "File does not exist.", True),
    ])
    run(tmp_path, date="2027-01-14", now=NOW - 86400)   # a manual run the day before: not a baseline
    _, md, _ = run(tmp_path)
    assert "Change is against the digest of 2027-01-08 (window 2027-01-01 to 2027-01-08, 2 errors)." in md
    assert "### python traceback, Bash: 2 (+1)." in md
    assert "### file or path missing, Read: 1 (new)." in md
    assert "Gone since 2027-01-08: network or HTTP error, Bash (was 1)." in md
    assert "## project p: 3 errors (+1), in 3 tool calls" in md
    assert "Error line: `ValueError: bad`" in md


def test_no_comparable_digest_means_no_change_column(tmp_path):
    t = Transcripts(tmp_path)
    t.session("a", str(tmp_path / "p"), [("Bash", {"command": "x"}, TRACE + "ValueError: bad", True)])
    run(tmp_path, date="2027-01-14", now=NOW - 86400)        # only yesterday's run
    run(tmp_path, date="2026-12-25", now=NOW - 21 * 86400)   # and one three weeks back
    _, md, _ = run(tmp_path)
    assert "No digest from 2027-01-06 to 2027-01-10 with a 7-day window, so there is no change column." in md
    assert "### python traceback, Bash: 1." in md and "Gone since" not in md
    assert "## project p: 1 error, in 1 tool calls" in md
    # a digest with another window length is no baseline either
    run(tmp_path, "--days", "3", date="2027-01-09", now=NOW - 6 * 86400)
    _, md, _ = run(tmp_path)
    assert "no change column" in md
    # a weekly run a day late still compares
    run(tmp_path, date="2027-01-09", now=NOW - 6 * 86400)
    _, md, _ = run(tmp_path)
    assert "Change is against the digest of 2027-01-09" in md


HDR = {"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"}


def block(v):
    return {"type": "input_text", "text": v if isinstance(v, str) else json.dumps(v)}


def mcp_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def codex_session(tmp_path, cwd, calls):
    """A Codex rollout file. calls: (kind, name, args or input, output) with kind function or custom."""
    d = tmp_path / "codex" / "2027" / "01" / "14"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "session_meta", "payload": {"id": "cx1", "cwd": cwd}}]
    for i, (kind, name, arg, out) in enumerate(calls):
        typ = "function_call" if kind == "function" else "custom_tool_call"
        call = {"type": typ, "name": name, "call_id": f"c{i}"}
        if "." in name:
            call["namespace"], call["name"] = name.split(".")
        call["arguments" if kind == "function" else "input"] = arg
        rows.append({"type": "response_item", "timestamp": TS, "payload": call})
        rows.append({"type": "response_item", "timestamp": TS,
                     "payload": {"type": typ + "_output", "call_id": f"c{i}", "output": out}})
    f = d / "rollout-x.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    os.utime(f, (NOW, NOW))


def test_codex_sessions(tmp_path):
    codex_session(tmp_path, str(tmp_path / ".myapp-lab" / "workspaces" / "lab"), [
        ("function", "exec_command", json.dumps({"cmd": "pytest"}),
         "Chunk ID: 1\nWall time: 0.0 seconds\nProcess exited with code 1\nOutput:\n/usr/bin/python: No module named pytest\n"),
        ("custom", "exec", "x", [HDR, block({"exit_code": 2, "output": "ls: cannot access 'z': No such file or directory"})]),
        ("function", "exec_command", "{}", "Exit code: 0\nWall time: 0.1\nfine"),
        # Exit code after a wrapper prefix
        ("function", "exec_command", json.dumps({"cmd": "python check.py"}),
         "Chunk ID: x\nWall time: 0.1\nExit code: 1\nOutput:\nfailure"),
    ])
    _, md, js = run(tmp_path, config=APP)
    assert js["calls"] == 4
    assert js["clusters"] == {"this PC\tpython module missing\texec_command": 1,
                              "app assistant\tfile or path missing\texec_command": 1,
                              "app assistant\tnon-zero exit (other)\texec_command": 1}
    assert "codex `cx1`" in md


def test_codex_exec_results_mcp_errors_and_batches(tmp_path):
    db = 'text(await tools.mcp__db__query({sql: "select Wild from t"}))'
    two = ('const r = await Promise.allSettled([tools.exec_command({cmd: "make a"}), '
           'tools.exec_command({cmd: "make b"})]); text(r)')
    ok_then_fail = 'text(await tools.exec_command({cmd:"true"})); text(await tools.exec_command({cmd:"false"}))'
    greps = 'text(await Promise.all([tools.exec_command({cmd:"rg x a"}), tools.exec_command({"cmd":"grep -q y b"})]))'
    codex_session(tmp_path, str(tmp_path / ".myapp-lab" / "workspaces" / "lab"), [
        # an MCP error inside exec (the real shape: one block per text() call)
        ("custom", "exec", db, [HDR, block(mcp_error("SQLite: no such column: Wild"))]),
        # a success block, then a failure: each block keeps its own exit code and output
        ("custom", "exec", ok_then_fail, [HDR, block({"exit_code": 0, "output": "OK"}),
                                          block({"exit_code": 2, "output": "make: *** No rule to make target"})]),
        # two failures in one Promise.allSettled block: two errors
        ("custom", "exec", two, [HDR, block([{"status": "fulfilled", "value": {"chunk_id": "a", "exit_code": 2,
                                                                              "output": "fatal: not a git repository"}},
                                             {"status": "fulfilled", "value": {"chunk_id": "b", "exit_code": 2,
                                                                              "output": "fatal: bad revision"}}])]),
        # a truncated JSON result after a warning
        ("custom", "exec", "x", [HDR, block('Warning: truncated output (original token count: 9)\nTotal output lines: 2\n\n'
                                            '{"chunk_id":"5","exit_code":2,"output":"sqlite3: Error: no such table: zz\\nmore')]),
        # a long-running cell, finished by a wait call: the MCP tool comes from the exec script of that cell
        ("custom", "exec", 'text(await tools.mcp__t3_code__preview_navigate({tabId: "t"}))',
         "Script running with cell ID 7\nWall time 1.0 seconds\nOutput:\n"),
        ("function", "wait", json.dumps({"cell_id": "7"}),
         [HDR, block(mcp_error("PreviewAutomationTimeoutError: navigate timed out after 10000ms."))]),
        # a script error
        ("custom", "exec", "x(", [{"type": "input_text", "text": "Script failed\nWall time 0.0 seconds\nOutput:\n"},
                                  block("Script error:\nSyntaxError: Unexpected end of input")]),
        # a direct MCP call with a namespace, error after a Wall time prefix
        ("function", "mcp__t3_code.preview_snapshot", "{}",
         'Wall time: 0.2 seconds\nOutput:\n{"error":{"_tag":"PreviewAutomationExecutionError","operation":"snapshot"}}'),
        # silent exit 1 from batched searches: fine
        ("custom", "exec", greps, [HDR, block([{"chunk_id": "c", "exit_code": 1, "output": ""},
                                               {"chunk_id": "d", "exit_code": 1, "output": ""}])]),
        # an error object a script printed itself
        ("custom", "exec", db, [HDR, block({"isError": True, "keys": ["error"], "error": "no such column: Sex"})]),
        # a single-string exec output: header and payload in one string, with an exec result and an MCP error
        ("custom", "exec", "x", "Script completed\nWall time 0.1 seconds\nOutput:\n"
         + json.dumps({"exit_code": 2, "output": "fatal: single string"})),
        ("custom", "exec", db, "Script completed\nWall time 0.1 seconds\nOutput:\n" + json.dumps(mcp_error("no such column: Y"))),
        # a script that logs a status line is no failed exec_command
        ("custom", "exec", "x", [HDR, block("step 3\nExit code: 1\nretrying ok")]),
        # text that merely mentions isError is no error
        ("custom", "exec", "x", [HDR, block('docs say "isError":true marks a failure')]),
    ])
    _, md, js = run(tmp_path, config=APP)
    cl = js["clusters"]
    assert cl.pop("app assistant\tno such column\tmcp db/query") == 3
    assert cl.pop("app assistant\tgit error\texec_command") == 3
    # make's failure after a success block, and the truncated result, each with its own output
    assert cl.pop("app assistant\tnon-zero exit (other)\texec_command") == 2
    assert cl.pop("app assistant\ttimeout\tmcp t3_code/preview_navigate") == 1
    assert cl.pop("app assistant\tsyntax error\texec") == 1
    assert cl.pop("app assistant\tMCP error (other)\tmcp t3_code/preview_snapshot") == 1
    assert cl == {}
    assert js["fine"] == {"exit 1 with no output from grep, rg, diff, cmp, test or pgrep": 2}
    assert js["calls"] == 13                        # a wait is no new tool call
    assert "No rule to make target" in md and "`OK`" not in md


def test_categorize_uses_error_lines_after_a_long_listing():
    text = "Exit code 1\n" + "\n".join(f"file{i}.txt" for i in range(400)) + "\nfatal: not a git repository"
    assert scan.categorize("Bash", text) == "git error"
    assert scan.categorize("mcp__db__query", '{"error":"SQLite: no such table: generate_series"}') == "no such table"
    assert scan.categorize("Bash", "Exit code 144\n") == "killed (exit 137, 143 or 144)"


def test_install_retro_dry_run(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run", "--retro"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "orchestack-retro.timer (Mondays 06:00)" in p.stdout
    assert "would run: systemctl --user enable --now orchestack-retro.timer" in p.stdout
    assert not (tmp_path / ".config" / "systemd").exists()
    p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run"], env=env, capture_output=True,
                       text=True)
    assert "orchestack-retro" not in p.stdout
    timer = (REPO / "retro" / "orchestack-retro.timer").read_text()
    assert "OnCalendar=Mon *-*-* 06:00:00" in timer and "Persistent=true" in timer
    assert "@REPO@/retro/scan.py" in (REPO / "retro" / "orchestack-retro.service").read_text()


def test_scrub_check_catches_a_partial_id_and_a_leak_in_unpushed_commits(tmp_path):
    leak = "0a1b2c3d" + "-1234-" + "5678/run"          # the start of a session id, built at run time
    f = tmp_path / "fixture.py"
    f.write_text(f"path = '/x/{leak}'\n")
    env = {**os.environ, "ORCHESTACK_SCRUB_PATTERNS": str(tmp_path / "none")}
    p = subprocess.run(["bash", str(REPO / "scripts" / "scrub_check.sh"), str(f)], capture_output=True, text=True, env=env)
    assert p.returncode == 1, p.stdout
    # a leak added by one unpushed commit and removed by the next is still found with --unpushed
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t" + AT + "t.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    (work / "scripts").mkdir()
    (work / "scripts" / "scrub_check.sh").write_text((REPO / "scripts" / "scrub_check.sh").read_text())
    (work / "a.txt").write_text("clean\n")
    subprocess.run(git + ["-C", str(work), "add", "."], check=True)
    subprocess.run(git + ["-C", str(work), "commit", "-qm", "base"], check=True)
    subprocess.run(git + ["-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    (work / "a.txt").write_text(f"{leak}\n")
    subprocess.run(git + ["-C", str(work), "commit", "-qam", "leak"], check=True)
    (work / "a.txt").write_text("clean\n")
    subprocess.run(git + ["-C", str(work), "commit", "-qam", "fix"], check=True)
    sc = ["bash", str(work / "scripts" / "scrub_check.sh")]
    assert subprocess.run(sc, cwd=work, capture_output=True, text=True, env=env).returncode == 0
    p = subprocess.run(sc + ["--unpushed"], cwd=work, capture_output=True, text=True, env=env)
    assert p.returncode == 1 and "unpushed commits" in p.stdout
