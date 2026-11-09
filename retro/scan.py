#!/usr/bin/env python3
"""retro/scan.py: weekly digest of the tool errors agents worked around. Read-only, no model calls, stdlib only.

    retro/scan.py [--days 7] [--out DIR] [--date YYYY-MM-DD] [--config FILE] [--claude-root DIR ...]
                  [--codex-root DIR ...] [--now EPOCH]

Reads Claude Code transcripts (every ~/.claude*/projects by default, `api-request*` lines skipped) and Codex
sessions (~/.codex/sessions) changed in the last N days, and keeps the tool results inside the window that
failed. Each error gets a category, a tool (for MCP: server and tool) and an owner:

- an owner from config.json `retro.owners` whose `cwd` regex matches the transcript's working directory
  (e.g. an app assistant's workspaces),
- "this PC" for interpreter, venv, PATH, hook, permission and missing-command errors, wherever they happened,
- otherwise the project: the git repository of the working directory (a worktree counts as its main repo).

Cases that are fine are left out and only counted: a user rejecting a call, an interrupted call, a call cancelled
because a sibling failed, exit 1 with no output from a single grep, rg, diff, cmp, test or pgrep command, and
exit 1 with output from one of them as the last program. Other silent failures are kept.

Writes <out>/<date>.md (the digest) and <out>/<date>.json (counts, read by next week's run for the change),
both mode 0600. Each cluster lists its count, the change since the digest of the previous window (same length,
dated one window earlier, give or take 2 days for a week, else no change column) and up to 3 examples: account,
session id, transcript line, tool, the first 300 characters of the error with secrets redacted, and the line that
says what failed when the start does not show it. It never copies
whole transcripts. Prints only the digest path and totals, so the journal holds no error text.

Config (`$ORCHESTACK_CONFIG_DIR/config.json`, default ~/.config/orchestack), all keys optional:
    "retro": {"claude_roots": ["~/.claude/projects", "~/.claude-*/projects"], "codex_roots": ["~/.codex/sessions"],
              "out_dir": "~/.local/state/orchestack/retro", "days": 7,
              "pc_tools": ["^mcp__t3-code__"],
              "owners": [{"name": "app assistant", "cwd": ["/\\.myapp-lab/workspaces"], "own_permissions": true}]}

`pc_tools`: tool-name regexes whose errors belong to this PC wherever they happen (shared MCP servers). An owner
with `own_permissions` keeps its hook, permission and sandbox blocks, because its workspace has its own rules.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys
import time

PC = "this PC"
EXAMPLES = 3
HEAD = 300

# (category, regex). The first match on the start of the error wins. Order matters.
CATS = [
    ("user rejected the call", r"user doesn't want to proceed|the tool use was rejected|rejected by the user|user declined"),
    ("interrupted", r"^the tool call was interrupted|^\[request interrupted|^<error>command was aborted before completion"),
    ("cancelled after a sibling call failed", r"sibling tool call errored|cancelled: parallel tool call"),
    ("blocked by a hook", r"^(pretooluse|posttooluse|userpromptsubmit|stop)\b[^\n]*hook|hook (error|returned|blocked|denied)|blocked by [^\n]*hook"),
    ("blocked: foreground sleep", r"^blocked: sleep \d"),
    ("blocked by a permission rule", r"^permission to use |requested permissions to|safety check denied|requires approval|"
     r"can't be checked before it runs|not allowed by (the )?(policy|rule|settings)|permission (rule|denied for tool)"),
    ("sandbox or operation not permitted", r"sandbox|operation not permitted"),
    ("command not found", r"command not found|not found in path|no such command|is not recognized as|exit code 127\b"),
    ("python module missing", r"modulenotfounderror|no module named|cannot find module"),
    ("wrong interpreter or venv", r"externally-managed-environment|bad interpreter|no such file or directory: '?[^\s']*(venv|python)|virtualenv"),
    ("Edit string not found or ambiguous", r"string to replace not found|found \d+ matches of the string"),
    ("file not read first or changed since read", r"has not been read yet|modified since (it was )?read|read it first"),
    ("tool input or schema error", r"inputvalidationerror|invalid (tool )?parameters|no such tool|unknown tool|required parameter|input schema|missing key at"),
    ("MCP server not connected or signed out", r"mcp server [^\n]* (is not connected|needs you to sign in)"),
    ("file or path missing", r"no such file or directory|does not exist|enoent|file not found|cannot access|path not found|: not found"),
    ("OS permission denied", r"permission denied|eacces"),
    ("timeout", r"timed out|timeout|^exit code 124\b"),
    ("killed (exit 137, 143 or 144)", r"^exit code (137|143|144)\b"),
    ("output or file too large", r"exceeds maximum|too large|token limit|exceeds the|maximum allowed"),
    ("git error", r"(^|\n)fatal:|error: pathspec|not a git repository|\[rejected\]"),
    ("network or HTTP error", r"could not resolve host|connection refused|econnrefused|econnreset|http (error )?[45]\d\d|status code [45]\d\d|ssl"),
    ("python traceback", r"traceback \(most recent call last\)"),
    ("syntax error", r"syntax error|syntaxerror|unexpected token|unexpected eof"),
    ("test or check failed", r"\bfailed\b|assertionerror|\bfail\b"),
]
CATS = [(n, re.compile(p, re.I)) for n, p in CATS]
# The error lines of a long output, used to categorize it and shown beside the head.
ERR_LINE = re.compile(r"\b(error|fatal|failed|cannot|can't|no such|not found|denied|invalid|unknown|refused|exception)\b", re.I)
FINE = {"user rejected the call", "interrupted", "cancelled after a sibling call failed"}
PC_CATS = {"blocked by a hook", "blocked by a permission rule", "blocked: foreground sleep",
           "sandbox or operation not permitted", "command not found", "python module missing",
           "wrong interpreter or venv"}
# Categories an owner with "own_permissions" keeps (its workspace has its own hooks and permission rules).
PERMISSION_CATS = {"blocked by a hook", "blocked by a permission rule", "sandbox or operation not permitted"}
PC_TEXT = re.compile(r"\bvenv\b|virtualenv|\$PATH\b|not in PATH|interpreter|pip install|uv (run|sync)", re.I)
# MCP errors: classes from the server's own message, tried before the general categories.
MCP_CLASSES = [
    ("no such column", r"no such column|unknown column|column [^\n]{0,60} does not exist"),
    ("no such table", r"no such table|unknown (table|sheet)"),
    ("SQL syntax error", r"sqlite: [^\n]*syntax error|near \"[^\"]*\": syntax error"),
    ("row not found", r"no row with id|record not found"),
]
MCP_CLASSES = [(n, re.compile(p, re.I)) for n, p in MCP_CLASSES]
NO_MATCH_PROGS = {"grep", "egrep", "fgrep", "zgrep", "rg", "diff", "cmp", "test", "[", "[[", "pgrep", "pkill", "which", "ag"}

# --- redaction -------------------------------------------------------------------------------------------------

# Values. A quoted value is consumed whole, escapes included and with no length limit. A quote that does not close
# on its line (the text was cut, or the quote is escaped in a way we do not parse) is redacted to the end of the line.
Q_DOUBLE = r'"(?:[^"\\\n]|\\.)*"'
Q_SINGLE = r"'[^'\n]*'"
Q_ESCAPED = r'\\"[^\n]*?\\"(?=[\s,}\];&)]|$)'          # \"...\" inside a JSON string, up to \" before a delimiter
Q_OPEN = r"""(?:\\"|["'])[^\n]*"""
BARE = r"""[^\s'",;&\\]+"""
SECRET_VALUE = f"(?:{Q_ESCAPED}|{Q_DOUBLE}|{Q_SINGLE}|{Q_OPEN}|{BARE})"
R = "<redacted>"
NOT_DONE = r"(?!\s*<(?:redacted|token|hex|user|email|jwt)>)"   # whitespace too, so backtracking cannot re-match

# Words that name a secret, in flags and in `key=value`, `key: value`, JSON and environment variables.
SECRET_WORD = (r"(?:token|secret|passw(?:or)?d|passwd|pwd|pw|passphrase|pass|api[ _-]?key|apikey|access[_-]?key|"
               r"private[_-]?key|[_.-]key|client[_-]?secret|session(?:[_-]?id)?|cookie|auth|authorization|"
               r"credentials?|creds?|signature|sig|jwt|[_.-]sk)")
# In key=value, a bare "pass" is test output (`PASS: ...`). Only `db_pass`, `smtp.pass` and the like count.
SECRET_WORD_KV = SECRET_WORD.replace("|pass|", "|[_.-]pass|")
HEADER = (r"(?:set-cookie|cookie|proxy-authorization|authorization|x-api-key|api-key|x-auth-token|private-token|"
          r"x-goog-api-key|x-amz-security-token)")

REDACT = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----.*?(-----END [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----|$)", re.S),
     "<private key>"),
    # Whole header values (every cookie, every Digest field), ended by whatever encloses the header:
    # a JSON value, a single-quoted or double-quoted shell argument, a JSON-escaped string, else the line end.
    (re.compile(r'(\\?"' + HEADER + r'\\?"\s*:\s*)' + NOT_DONE + f"(?:{Q_ESCAPED}|{Q_DOUBLE}|{Q_OPEN})", re.I),
     r"\1" + R),
    (re.compile(r"('\s*" + HEADER + r"\s*:\s*)" + NOT_DONE + r"[^'\n]*", re.I), r"\1" + R),
    (re.compile(r'(\\"\s*' + HEADER + r"\s*:\s*)" + NOT_DONE + r'[^\n]*?(?=\\"|$)', re.I | re.M), r"\1" + R),
    (re.compile(r'((?<!\\)"\s*' + HEADER + r"\s*:\s*)" + NOT_DONE + r'(?:[^"\\\n]|\\.)*', re.I), r"\1" + R),
    (re.compile(r"(\b" + HEADER + r"\s*:\s*)" + NOT_DONE + r"[^\n]*", re.I), r"\1" + R),
    # credentials in a URL: scheme://user:password@ and scheme://<long token>@, and webhook URLs
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@'\"]*:[^/\s@'\"]*@", re.I), r"\1<user>:" + R + "@"),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[A-Za-z0-9_.~%-]{16,}@", re.I), r"\1" + R + "@"),
    (re.compile(r"\b(hooks\.slack\.com/(?:services|workflows|triggers)/|discord(?:app)?\.com/api/webhooks/|"
                r"api\.telegram\.org/bot)[^\s'\"<>]+", re.I), r"\1" + R),
    (re.compile(r"(--cookie)(\s+|=)" + SECRET_VALUE), r"\1\2" + R),
    (re.compile(r"\b(bearer)\s+" + NOT_DONE + r"[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 " + R),
    (re.compile(r"\b(token)\s+" + NOT_DONE + r"(?=[A-Za-z._~+/=-]*[0-9]|[A-Za-z0-9._~+/=-]{20,})[A-Za-z0-9._~+/=-]{8,}",
                re.I), r"\1 " + R),
    # command-line flags: --password x, --api-key=x, --token "two words", --creds user:pass
    (re.compile(r"(--?[\w-]*" + SECRET_WORD + r"[\w-]*)(\s+|=)(?!-)" + NOT_DONE + SECRET_VALUE, re.I), r"\1\2" + R),
    # -p with a password: mysql -pX, sshpass -p X, docker / podman / skopeo / helm login -p X
    (re.compile(r"\b((?:mysql|mysqldump|mysqladmin|mariadb|mariadb-dump)\b[^\n|;&]*?\s-p)(?!\s)" + NOT_DONE + SECRET_VALUE),
     r"\1" + R),
    (re.compile(r"\b(sshpass\s+-p\s*)" + NOT_DONE + SECRET_VALUE), r"\1" + R),
    (re.compile(r"\b((?:docker|podman|skopeo|buildah|nerdctl|helm|oras|crane)\b[^\n|;&]*?\blogin\b[^\n|;&]*?\s-p)(\s+|=)?"
                + NOT_DONE + SECRET_VALUE), r"\1\2" + R),
    (re.compile(r"(\s(?:-u|--user)\s+)[^\s:'\"]+:" + SECRET_VALUE), r"\1<user>:" + R),
    (re.compile(r"\b(login\s+\S+\s+password\s+)" + SECRET_VALUE), r"\1" + R),
    # environment-style names with a space before the value (printenv-like listings): AWS_SECRET_ACCESS_KEY <v>
    (re.compile(r"\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PASSPHRASE|_KEY|_PW|_SK)[A-Z0-9_]*)([ \t]+)" + NOT_DONE
                + r"(?=\S*[0-9a-z])\S{8,}"), r"\1\2" + R),
    # space-separated api keys and secret keys: `apikey abcd1234`, `secret key: x`
    (re.compile(r"\b((?:api|secret|access)[ _-]?key\s*:?\s+)" + NOT_DONE + r"(?=\S*\d)\S{6,}", re.I), r"\1" + R),
    # key=value, key: value, "key": "value", KEY=value (the secret word ends the name, or comes before _suffix)
    (re.compile(r"\b([\w.-]*" + SECRET_WORD_KV + r"(?:[_-][\w-]*)?)\b(\\?[\"']?\s*[:=]\s*)" + NOT_DONE + SECRET_VALUE,
                re.I), r"\1\2" + R),
    # tokens with known prefixes
    (re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                r"github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
                r"AIza[0-9A-Za-z_-]{30,}|glpat-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{30,}|"
                r"pypi-[A-Za-z0-9_-]{30,}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|tskey-[A-Za-z0-9-]{16,}|"
                r"dop_v1_[a-f0-9]{40,}|shp(?:at|ss|ca)_[a-fA-F0-9]{24,}|\d{6,}:AA[A-Za-z0-9_-]{30,})"), "<token>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "<jwt>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    # 32 or more hex characters: a key or a hash, the scan cannot tell which
    (re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])"), "<hex>"),
]
# A long run of base64 or URL-safe characters (slashes too, so base64 split by `/` stays one run).
LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{24,}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _class(c):
    return "a" if c.islower() else "A" if c.isupper() else "0" if c.isdigit() else "-"


def _changes(s):
    return sum(_class(x) != _class(y) for x, y in zip(s, s[1:]))


def _long(m):
    """A long run of letters, digits and symbols that changes character class often is a key, not a word, a path
    or a file name (`MothSorter`, `run_explore_20260101` change class rarely). A run with slashes is a key when
    most of its parts look like key material (base64 split by `/`), else a path whose long parts are judged one by
    one. Ids (UUIDs) stay."""
    s = m.group(0)
    if "/" not in s:
        return "<token>" if _keyish(s) else s
    parts = [x for x in s.split("/") if x]
    keyish = sum(len(x) for x in parts if _keyish(x, 10))
    if keyish * 2 >= sum(len(x) for x in parts):
        return "<token>"
    return "/".join(_long(re.match(r".*", x)) if len(x) >= 24 else x for x in s.split("/"))


def _keyish(s, least=24):
    if len(s) < least or UUID.search(s):
        return False
    kinds = {_class(c) for c in s}
    if {"a", "A", "0"} <= kinds and _changes(s) > len(s) / 3:
        return True
    # one letter case and digits (base36 keys, upper-case keys), judged per part between - and _
    for seg in re.split(r"[-_]", s):
        k = {_class(c) for c in seg}
        if (len(seg) >= 24 and k in ({"a", "0"}, {"A", "0"}) and sum(c.isdigit() for c in seg) >= 4
                and _changes(seg) > len(seg) / 4):
            return True
    return False


def redact(text):
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    return LONG_TOKEN.sub(_long, text)


RAW = HEAD * 60


def head(text, n=HEAD):
    """The first n characters of an error, redacted. Redaction runs on the raw text (line ends kept, so a header
    ends where its line ends), on at most RAW characters. When the text was longer, its cut-off final word is
    dropped first: its end is missing, so the patterns might not recognize it. Then whitespace is collapsed and
    the result cut to n characters."""
    t = text[:RAW]
    if len(text) > RAW:
        t = re.sub(r"\S*\Z", "", t)
    return re.sub(r"\s+", " ", redact(t)).strip()[:n].rstrip()


def error_lines(text, n=5):
    """Up to n lines that look like errors, from the first 20,000 characters (an error often follows a long listing)."""
    out = []
    for ln in text[:20000].splitlines():
        if ERR_LINE.search(ln):
            out.append(ln.strip())
            if len(out) >= n:
                break
    return out


def error_line(text, shown):
    """The line that says what failed, redacted, if the head does not show it: a traceback's final exception line,
    else the first error-looking line. Else ''."""
    if re.search(r"traceback \(most recent call last\)", text[:4000], re.I):
        lines = [ln for ln in text[:20000].splitlines()
                 if re.match(r"\s*\w+(\.\w+)*(Error|Exception|Exit|Interrupt)\b", ln)]
    else:
        lines = error_lines(text, 1)
    if not lines:
        return ""
    line = head(lines[-1], 150)
    return "" if line[:60] in shown else line


# --- classification ---------------------------------------------------------------------------------------------

def tool_key(name):
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"mcp {parts[1]}/{'__'.join(parts[2:]) or '?'}"
    return name or "?"


def first_prog(cmd):
    cmd = re.sub(r"^\s*((if|then|else|elif|do|while|until|time|!|\{|\()\s+|\w+=\S*\s+)*", "", cmd or "")
    tok = re.split(r"[\s;|&()]+", cmd.strip(), maxsplit=1)[0] if cmd.strip() else ""
    return os.path.basename(tok)


def progs(cmd):
    """First program of each pipeline or list segment of a shell command."""
    return [p for p in (first_prog(seg) for seg in re.split(r"\|\|?|&&|;|\n", cmd or "") if seg.strip())
            if p not in ("fi", "done", "}", ")", "")]


def strip_exit(text):
    """(exit code or None, output after the exit-code line)."""
    m = re.match(r"\s*Exit code:? (\d+)\s*\n?", text)
    if m:
        return int(m.group(1)), text[m.end():]
    return None, text


def categorize(tool, text):
    h = re.sub(r"</?tool_use_error>", "", text[:1500]).strip()
    if tool.startswith("mcp__"):
        m = re.search(r'"code"\s*:\s*"(\w+)"', h)
        if m and m.group(1) not in ("invalid_request",):
            return f"MCP {m.group(1)}"
        for n, rx in MCP_CLASSES:
            if rx.search(h):
                return n
    _, rest = strip_exit(h)
    rest = rest + "\n" + "\n".join(error_lines(text))
    for n, rx in CATS:
        if rx.search(rest) or (n in ("command not found", "timeout", "killed (exit 137, 143 or 144)") and rx.search(h)):
            return n
    if tool.startswith("mcp__"):
        return "MCP error (other)"
    if re.match(r"\s*Exit code", h):
        return "non-zero exit (other)"
    return "other error"


def fine_reason(cat, text, cmd):
    """Why an error result is fine and left out, or None. Exit 1 is fine only from a search or test program: with
    no output only when the command is that program alone (no match, a false test), because in a compound
    command another step may have failed silently; with output when it ran last (e.g. diff)."""
    if cat in FINE:
        return cat
    code, rest = strip_exit(text)
    if code == 1:
        ps = progs(cmd)
        if re.fullmatch(r"\s*(0\s*)?", rest) and len(ps) == 1 and ps[0] in NO_MATCH_PROGS:
            return "exit 1 with no output from grep, rg, diff, cmp, test or pgrep"
        if ps and ps[-1] in NO_MATCH_PROGS and not re.search(
                r"traceback|error|no such file|not found|denied", rest[:20000], re.I):
            return "exit 1 from grep, rg, diff, cmp, test or pgrep"
    return None


# --- owners -----------------------------------------------------------------------------------------------------

class Owners:
    def __init__(self, rules, pc_tools=()):
        self.rules = [(r["name"], [re.compile(p) for p in r.get("cwd", [])], bool(r.get("own_permissions")))
                      for r in rules or [] if r.get("name")]
        self.pc_tools = [re.compile(p) for p in pc_tools or ()]
        self.cache = {}
        self.home = os.path.expanduser("~").rstrip("/")

    def of(self, cwd):
        return self.rule(cwd)[0]

    def rule(self, cwd):
        """(owner name, whether it keeps its own permission blocks) for a working directory."""
        if cwd not in self.cache:
            hit = next(((n, own) for n, rxs, own in self.rules if any(rx.search(cwd or "") for rx in rxs)), None)
            self.cache[cwd] = hit or ("project " + self.project(cwd), False)
        return self.cache[cwd]

    def project(self, cwd):
        if not cwd:
            return "(unknown)"
        d = cwd.rstrip("/")
        while d and d != "/" and d != self.home:
            g = os.path.join(d, ".git")
            try:
                if os.path.isdir(g):
                    return os.path.basename(d)
                if os.path.isfile(g):
                    m = re.match(r"gitdir:\s*(\S+)", open(g, errors="replace").read())
                    if m and "/.git/worktrees/" in m.group(1):
                        return os.path.basename(m.group(1).split("/.git/worktrees/")[0])
                    return os.path.basename(d)
            except OSError:
                pass
            d = os.path.dirname(d)
        if cwd.rstrip("/") == self.home:
            return "~ (home, no repo)"
        if re.search(r"/(scratch|scratchpad)(/|$)|^/tmp/claude-", cwd):
            return "(scratch, no repo)"
        return re.sub(r"-t3-[0-9a-f]{6,}$", "", os.path.basename(cwd.rstrip("/"))) or cwd


# --- scanning ---------------------------------------------------------------------------------------------------

class Scan:
    def __init__(self, owners, cutoff):
        self.owners = owners
        self.cutoff = cutoff
        self.cut_iso = datetime.datetime.fromtimestamp(cutoff, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        self.calls = collections.Counter()      # owner of cwd -> tool calls
        self.errors = collections.Counter()     # owner (after the PC rule) -> errors kept
        self.fine = collections.Counter()       # reason -> count
        self.clusters = {}                      # (owner, category, tool) -> dict
        self.sessions = set()
        self.files = 0

    def add_error(self, *, account, session, path, lineno, cwd, tool, text, cmd):
        cat = categorize(tool, text)
        why = fine_reason(cat, text, cmd)
        if why:
            self.fine[why] += 1
            return
        where, own_permissions = self.owners.rule(cwd)
        if own_permissions and cat in PERMISSION_CATS:
            owner = where
        elif (cat in PC_CATS or any(rx.search(tool) for rx in self.owners.pc_tools)
              or any(PC_TEXT.search(ln) for ln in error_lines(text))):
            owner = PC
        else:
            owner = where
        key = (owner, cat, tool_key(tool))
        c = self.clusters.setdefault(key, {"count": 0, "from": collections.Counter(), "examples": [], "seen": set()})
        c["count"] += 1
        c["from"][where] += 1
        self.errors[owner] += 1
        if len(c["examples"]) < EXAMPLES and session not in c["seen"]:
            c["seen"].add(session)
            c["examples"].append({"account": account, "session": session, "path": path, "line": lineno,
                                  "tool": tool, "from": where, "head": head(text),
                                  "last": error_line(text, head(text))})

    def claude_file(self, path, account):
        uses = {}
        cwd = ""
        session = os.path.splitext(os.path.basename(path))[0]
        if "/subagents/" in path:
            session = path.split("/subagents/")[0].rsplit("/", 1)[-1] + "/" + session
        active = False
        with open(path, errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if '"tool_' not in line or '"api-request' in line[:300]:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") not in ("assistant", "user"):
                    continue
                cwd = d.get("cwd") or cwd
                ts = d.get("timestamp") or ""
                if ts and ts[:19] < self.cut_iso:
                    continue
                m = d.get("message")
                if not isinstance(m, dict) or not isinstance(m.get("content"), list):
                    continue
                for b in m["content"]:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        name = b.get("name") or "?"
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        uses[b.get("id")] = (name, inp.get("command") if name == "Bash" else None)
                        self.calls[self.owners.of(cwd)] += 1
                        active = True
                    elif b.get("type") == "tool_result" and b.get("is_error") is True:
                        name, cmd = uses.get(b.get("tool_use_id"), ("?", None))
                        self.add_error(account=account, session=session, path=path, lineno=lineno, cwd=cwd,
                                       tool=name, text=text_of(b.get("content")), cmd=cmd)
        if active:
            self.sessions.add((account, session))

    def codex_file(self, path):
        uses = {}
        cells = {}                              # exec cell id -> the exec call's script (for `wait` results)
        cwd = ""
        session = os.path.splitext(os.path.basename(path))[0]
        active = False
        with open(path, errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if '"session_meta"' not in line and '"turn_context"' not in line and "call" not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                if d.get("type") in ("session_meta", "turn_context"):
                    cwd = p.get("cwd") or cwd
                    if d.get("type") == "session_meta":
                        session = p.get("id") or session
                    continue
                ts = d.get("timestamp") or ""
                kind = p.get("type")
                if kind in ("function_call", "custom_tool_call", "local_shell_call"):
                    name = p.get("name") or kind
                    if p.get("namespace") and str(p["namespace"]).startswith("mcp__"):
                        name = f"{p['namespace']}__{name}"
                    raw = p.get("arguments") if kind == "function_call" else p.get("input")
                    raw = raw if isinstance(raw, str) else ""
                    cmd = None
                    if name in ("exec_command", "shell"):
                        try:
                            a = json.loads(raw or "{}")
                            cmd = a.get("cmd") or a.get("command")
                            cmd = " ".join(cmd) if isinstance(cmd, list) else cmd
                        except (ValueError, AttributeError):
                            pass
                    elif name == "wait":
                        m = re.search(r'"cell_id"\s*:\s*"?(\w+)', raw)
                        raw = cells.get(m.group(1), "") if m else ""
                    uses[p.get("call_id")] = (name, cmd, raw)
                    if ts and ts[:19] < self.cut_iso:
                        continue
                    if name != "wait":
                        self.calls[self.owners.of(cwd)] += 1
                        active = True
                elif kind in ("function_call_output", "custom_tool_call_output"):
                    name, cmd, raw = uses.get(p.get("call_id"), ("?", None, ""))
                    out = p.get("output")
                    if name == "exec":
                        m = re.match(r"Script running with cell ID (\w+)", text_of(out))
                        if m:
                            cells[m.group(1)] = raw
                    if ts and ts[:19] < self.cut_iso:
                        continue
                    for tool, text, c in codex_errors(name, cmd, raw, out):
                        self.add_error(account="codex", session=session, path=path, lineno=lineno, cwd=cwd,
                                       tool=tool, text=text, cmd=c)
        if active:
            self.sessions.add(("codex", session))


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
    if isinstance(c, dict):
        return json.dumps(c)
    return ""


def blocks_of(c):
    """The text blocks of a Codex output: a string is one block."""
    if isinstance(c, list):
        return [b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return [text_of(c)]


EXIT_LINE = re.compile(r"(?m)^(?:Process exited with code|Exit code:?) (\d+)\s*$")
WRAPPER = re.compile(r"\A(?:(?:Chunk ID|Wall time|Process exited with code|Exit code|Original token count|Session ID|"
                     r"Warning: truncated output|Total output lines)\b[^\n]*\n)+(?:\s*Output:\n)?")


EXEC_HEADER = re.compile(r"Script (completed|failed|running with cell ID \w+)\nWall time [^\n]*\n(?:Output:\n)?")


def decoded(block):
    """The JSON value in a text block, also after a prefix such as a truncation warning, or None."""
    t = block.strip()
    if not t or t[0] not in "{[":
        m = re.search(r"(?m)^[{\[]", t)
        if not m:
            return None
        t = t[m.start():]
    try:
        return json.JSONDecoder().raw_decode(t)[0]
    except ValueError:
        return None


def results(v, depth=0):
    """(kind, value) of each tool result inside decoded exec output: exec_command results ('exec'), MCP results
    ('mcp'), rejected promises ('rejected'), also inside Promise.allSettled wrappers and lists."""
    if depth > 8:
        return
    if isinstance(v, dict):
        if isinstance(v.get("exit_code"), int) and not isinstance(v.get("exit_code"), bool) and (
                "output" in v or "chunk_id" in v or "wall_time_seconds" in v):
            yield "exec", v
        elif v.get("isError") is True:
            yield "mcp", v
        elif v.get("status") == "rejected" and "reason" in v:
            yield "rejected", v
        else:
            for x in list(v.values())[:500]:
                yield from results(x, depth + 1)
    elif isinstance(v, list):
        for x in v[:500]:
            yield from results(x, depth + 1)


def mcp_text(r):
    """The error text of an MCP result: its text content, else its error field, else the result itself."""
    t = text_of(r.get("content")) if isinstance(r.get("content"), list) else ""
    e = r.get("error")
    return t or (e if isinstance(e, str) else "") or json.dumps(r)[:2000]


def script_tools(script):
    """(MCP tool names, exec_command commands) that an exec script calls."""
    mcp = sorted(set(re.findall(r"\btools\.(mcp__\w+?)\s*\(", script or "")))
    cmds = []
    for q in re.findall(r"exec_command\(\s*\{\s*[\"']?cmd[\"']?\s*:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`[^`]*`)",
                         script or ""):
        try:
            cmds.append(json.loads(q) if q[0] == '"' else q[1:-1])
        except ValueError:
            cmds.append(q[1:-1])
    return mcp, cmds


def nested_errors(script, blocks):
    """The failed tool results inside the output blocks of an exec script, each with its own tool and output."""
    mcp, cmds = script_tools(script)
    mcp_tool = mcp[0] if len(mcp) == 1 else "exec (MCP tool)"
    # A command is known when the script runs one, or when every one it runs is a search or test.
    cmd = cmds[0] if len(cmds) == 1 or (cmds and all(progs(c)[-1:] and progs(c)[-1] in NO_MATCH_PROGS
                                                     for c in cmds)) else None
    out = []
    for b in blocks:
        v = decoded(b)
        if v is None:
            # a truncated JSON result: take the exit code and output from this block only
            m = re.search(r'(?m)^[\[{][^\n]*?"exit_code"\s*:\s*([1-9]\d*)', b[:4000])
            if m:
                o = re.search(r'"output"\s*:\s*"((?:[^"\\]|\\.)*)', b[m.end():m.end() + 20000])
                body = o.group(1).encode().decode("unicode_escape", "replace") if o else ""
                out.append(("exec_command", f"Exit code {m.group(1)}\n{body}", cmd))
            elif WRAPPER.match(b) and EXIT_LINE.search(b[:800]):
                e = exit_error(b)
                if e:
                    out.append(("exec_command", e, cmd))
            continue
        for kind, r in results(v):
            if kind == "exec" and r["exit_code"] != 0:
                out.append(("exec_command", f"Exit code {r['exit_code']}\n{text_of(r.get('output'))}", cmd))
            elif kind == "mcp":
                out.append((mcp_tool, mcp_text(r), None))
            elif kind == "rejected":
                reason = r["reason"]
                out.append(("exec", reason if isinstance(reason, str) else json.dumps(reason)[:2000], None))
    return out


def exit_error(text):
    """'Exit code N' and the output when a wrapped result names a non-zero exit code, '' for exit 0, None if none."""
    top = text[:800]
    m = EXIT_LINE.search(top.split("Output:\n", 1)[0] if "Output:\n" in top else top)
    if not m:
        return None
    if m.group(1) == "0":
        return ""
    rest = text[m.end():]
    out = rest.split("Output:\n", 1)[1] if "Output:\n" in rest[:300] else rest.lstrip("\n")
    return f"Exit code {m.group(1)}\n{out}"


def codex_errors(name, cmd, script, output):
    """Codex marks no result as an error. Returns [(tool, error text in Claude's 'Exit code N' form, command)]."""
    blocks = blocks_of(output)
    whole = "\n".join(blocks)
    if name in ("exec", "wait"):
        found = []
        first = blocks[0] if blocks else ""
        m = EXEC_HEADER.match(first)
        # the payload after the header: the rest of the first block (a single-string output) and the other blocks
        payload = ([first[m.end():]] if first[m.end():].strip() else []) + blocks[1:] if m else blocks
        if m and m.group(1) == "failed":
            found.append(("exec", "\n".join(payload), None))
        return found + nested_errors(script, payload)
    e = exit_error(whole)
    if e is not None:
        return [(name, e, cmd)] if e else []
    body = WRAPPER.sub("", whole)
    v = decoded(body)
    if v is not None:
        errs = [(name, f"Exit code {r['exit_code']}\n{text_of(r.get('output'))}", cmd) for k, r in results(v)
                if k == "exec" and r["exit_code"] != 0]
        errs += [(name, mcp_text(r), cmd) for k, r in results(v) if k == "mcp"]
        if errs:
            return errs
    if re.match(r"\s*(Script failed|collab (spawn|tool) failed|\{\"error\"|error:|Error:|failed to |[\w/]+ failed: )", body):
        return [(name, body, cmd)]
    return []


# --- output -----------------------------------------------------------------------------------------------------

def tilde(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home + "/") else p


def change(now, prev, have_prev=True):
    """' (+2)', ' (same)', ' (new)', or '' when there is no comparable digest."""
    if not have_prev:
        return ""
    if prev is None:
        return " (new)"
    d = now - prev
    return f" ({d:+d})" if d else " (same)"


def day(date, delta=0):
    return (datetime.date.fromisoformat(date) + datetime.timedelta(days=delta)).isoformat()


def render(scan, date, days, prev, roots):
    pc = prev.get("clusters", {}) if prev else {}
    po = prev.get("owners", {}) if prev else {}
    calls = sum(scan.calls.values())
    errs = sum(scan.errors.values())
    target, tol = baseline_target(date, days)
    if prev:
        pd = float(prev.get("days", days))
        base = (f" Change is against the digest of {prev['date']} (window {day(prev['date'], -round(pd))} to "
                f"{prev['date']}, {prev['errors']} errors).")
    else:
        base = (f" No digest from {day(target, -tol)} to {day(target, tol)} with a {days:g}-day window, so there is "
                f"no change column.")
    out = [f"# Tool error retro {date}", "",
           f"Window: last {days:g} days ({day(date, -round(days))} to {date}). "
           f"Sources: {', '.join(tilde(r) for r in roots)}.",
           f"{len(scan.sessions)} transcripts with tool calls, {calls} tool calls, {errs} errors kept "
           f"({errs / max(1, calls):.1%}), {sum(scan.fine.values())} fine errors left out." + base,
           "", "Each example: account, session id, transcript line (open it with `sed -n 'LINEp' FILE | cut -c1-3000`), "
           "tool, the owner of its working directory, and the error start, redacted.", ""]
    by_owner = collections.defaultdict(list)
    for (owner, cat, tool), c in scan.clusters.items():
        by_owner[owner].append((cat, tool, c))
    gone = collections.defaultdict(list)
    for k, n in pc.items():
        owner, cat, tool = k.split("\t")
        if (owner, cat, tool) not in scan.clusters:
            gone[owner].append((cat, tool, n))
    owners = sorted(set(by_owner) | set(gone), key=lambda o: (o != PC, -scan.errors.get(o, 0), o))
    for owner in owners:
        n = scan.errors.get(owner, 0)
        calls_here = f", in {scan.calls[owner]} tool calls" if scan.calls.get(owner) else ""
        out += [f"## {owner}: {n} error{'s' if n != 1 else ''}{change(n, po.get(owner), bool(prev))}{calls_here}", ""]
        for cat, tool, c in sorted(by_owner[owner], key=lambda x: (-x[2]["count"], x[0], x[1])):
            k = "\t".join((owner, cat, tool))
            frm = ""
            if owner == PC:
                frm = " From: " + ", ".join(f"{w} {v}" for w, v in c["from"].most_common(4)) + "."
            out.append(f"### {cat}, {tool}: {c['count']}{change(c['count'], pc.get(k), bool(prev))}.{frm}")
            for e in c["examples"]:
                last = f" Error line: `{e['last'].replace('`', chr(39))}`" if e["last"] else ""
                out.append(f"- {e['account']} `{e['session']}` `{tilde(e['path'])}:{e['line']}` {e['tool']}, "
                           f"from {e['from']}: `{e['head'].replace('`', chr(39))}`{last}")
            out.append("")
        if gone[owner]:
            out.append(f"Gone since {prev['date']}: " + ", ".join(f"{cat}, {tool} (was {n})" for cat, tool, n in sorted(gone[owner])) + ".")
            out.append("")
    if scan.fine:
        out += ["## Left out as fine", ""] + [f"- {k}: {v}" for k, v in scan.fine.most_common()] + [""]
    return "\n".join(out)


def write_private(path, text):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def baseline_target(date, days):
    """(the date a comparable digest should have, tolerance in days): one window earlier, give or take 2 days for
    a weekly scan (a timer that fires a day late still compares)."""
    return day(date, -round(days)), max(1, round(days * 2 / 7))


def previous(out_dir, date, days):
    """The digest of the previous window: same window length, dated one window earlier within the tolerance, the
    closest to that date. A manual run a day earlier is not a baseline. None if there is no such digest."""
    target, tol = baseline_target(date, days)
    t = datetime.date.fromisoformat(target)
    best = None
    for p in glob.glob(os.path.join(out_dir, "????-??-??.json")):
        d = os.path.basename(p)[:10]
        try:
            gap = abs((datetime.date.fromisoformat(d) - t).days)
        except ValueError:
            continue
        if d >= date or gap > tol:
            continue
        try:
            js = json.load(open(p))
        except (OSError, ValueError):
            continue
        if abs(float(js.get("days", days)) - days) > 0.01:
            continue
        if best is None or gap < best[0] or (gap == best[0] and d > best[1]):
            best = (gap, d, js)
    return best[2] if best else None


def load_config(path):
    path = path or os.path.join(os.environ.get("ORCHESTACK_CONFIG_DIR") or os.path.expanduser("~/.config/orchestack"),
                                "config.json")
    try:
        cfg = json.load(open(path))
    except (OSError, ValueError):
        return {}
    r = cfg.get("retro") if isinstance(cfg, dict) else None
    return r if isinstance(r, dict) else {}


def expand(patterns):
    out = []
    for p in patterns:
        for d in sorted(glob.glob(os.path.expanduser(p))) or []:
            if os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def files(root, cutoff):
    for dp, _, fn in os.walk(root):
        for f in fn:
            if f.endswith(".jsonl"):
                p = os.path.join(dp, f)
                try:
                    if os.path.getmtime(p) >= cutoff:
                        yield p
                except OSError:
                    continue


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=float)
    ap.add_argument("--out")
    ap.add_argument("--date")
    ap.add_argument("--config")
    ap.add_argument("--claude-root", action="append")
    ap.add_argument("--codex-root", action="append")
    ap.add_argument("--now", type=float, help="epoch seconds (tests)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    now = a.now or time.time()
    days = a.days or float(cfg.get("days", 7))
    cutoff = now - days * 86400
    date = a.date or datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    out_dir = os.path.expanduser(a.out or cfg.get("out_dir") or "~/.local/state/orchestack/retro")
    claude = expand(a.claude_root or cfg.get("claude_roots") or ["~/.claude/projects", "~/.claude-*/projects"])
    codex = expand(a.codex_root or cfg.get("codex_roots") or ["~/.codex/sessions"])
    scan = Scan(Owners(cfg.get("owners"), cfg.get("pc_tools")), cutoff)
    t0 = time.time()
    for root in claude:
        account = os.path.basename(os.path.dirname(root.rstrip("/")))
        for p in files(root, cutoff):
            scan.files += 1
            scan.claude_file(p, account)
    for root in codex:
        for p in files(root, cutoff):
            scan.files += 1
            scan.codex_file(p)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    prev = previous(out_dir, date, days)
    md = render(scan, date, days, prev, claude + codex)
    summary = {"date": date, "days": days, "calls": sum(scan.calls.values()), "errors": sum(scan.errors.values()),
               "owners": dict(scan.errors), "fine": dict(scan.fine),
               "clusters": {"\t".join(k): c["count"] for k, c in scan.clusters.items()}}
    base = os.path.join(out_dir, date)
    write_private(base + ".md", md)
    write_private(base + ".json", json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"retro: {base}.md  files={scan.files} calls={summary['calls']} errors={summary['errors']} "
          f"fine={sum(scan.fine.values())} clusters={len(scan.clusters)} runtime_s={time.time() - t0:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
