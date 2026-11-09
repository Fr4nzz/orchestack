# orchestack

Skills and hooks for running coding-agent orchestrators in T3 Code (Claude Code through the Agent SDK, and Codex).

An orchestrator chat routes work to delegates, decides small things itself and waits cheaply. In practice
orchestrators forget that role after a compaction, wake themselves for nothing, and big chats burn the weekly
budget re-reading their own context. orchestack packs the rules that fixed this into three skills and two small
hooks.

## Why: what the usage audits found

Two audits of one user's week of T3 work (every local transcript, cost-weighted tokens, calibrated against the
plan's usage meter) found:

- Re-reading a context after the 1-hour cache expired was about **10 % of a week**. Half of one orchestrator's own
  cost was cache rewrites, mostly from a 3-hourly scheduled check.
- Background waits on cluster jobs that timed out after about 2 h returned to cold caches: about **4 % of a week**.
- A large writing chat did 40 long hands-on turns at about 505k context. Fresh workers would have saved about
  **60 % of that**.
- Delegates reused for unrelated follow-up tasks grew to 800 to 960k context.
- Approving a programme from token estimates instead of a measured pilot ran **about 8x over**, and that one
  programme took 38 % of all usage in its period.
- Images reviewed by small fresh workers cost about 2 % of a week per 300 items. The same kind of review in a few
  long agents cost about 55 %.
- An outdated copy of the rules pasted into a brief caused about 25 extra parent wakes.
- 30 AI scheduled checks that woke a large orchestrator every 3 to 6 h cost about **1.6 % of a week in 4 days**,
  and most of them found no change. The watcher below replaces them.
- T3 relays the end of every turn an async delegate task started to its parent, whatever it says. A launched lead
  (`t3_thread_launch`) is never relayed and wakes its parent only by sending: one 6-day lead ran 293 turns and woke
  its orchestrator 76 times.

## What is in it

| Piece | Say or trigger | What it does |
|---|---|---|
| `skills/orchestrator` | "you are the orchestrator", "orchestrate this", "work autonomously", "decide for me", "parallelize", "I'm going to sleep", chats titled `Orchestrator:` or `Lead:` | The playbook: parallel lanes via delegates, decide reversible steps, no "want me to...?", pilot pricing, pace, waits, briefs, models. |
| `skills/sol-loop` | "sol loop" | One review loop with two independent critics per round (GPT Sol + a fresh Opus), smallest fixes, a ledger, a stop rule. |
| `skills/bulk-image-review` | before agents look at many images | Cost model, ask-first threshold, and the small-fresh-worker pattern. |
| `skills/ui-loop` | "ui loop", "improve the UI" | Owner taste first, shots and metrics at real screen sizes, a hands-on pass acting as the user, two critics per round with rotating lenses, one implementer, re-measure. `scripts/ui_shoot.mjs` shoots and measures each view. |
| `hooks/role_card.py` | Claude Code `SessionStart` (`startup`, `resume`, `compact`) | Prints a 7-line role card into context for orchestrator and lead chats, so the role survives compaction. |
| `watcher/watch.py` | systemd `--user` timer, every 15 min (opt-in) | Wakes a chat only when something changed: jobs ended, a child failed, low disk, weekly reset or behind pace, an unanswered `decision:`. No model calls. |
| `retro/scan.py` + `skills/retro` | systemd `--user` timer, Mondays 06:00 (opt-in), then "retro" | The scan clusters the week's tool errors by owner (this PC, an app assistant, each project) into a private digest, free. The skill reads it and proposes at most 3 fixes per owner, tool first, CLAUDE.md last. |
| `hooks/memory_gate.py` | Claude Code `PreToolUse` on `Agent`, `delegate_task`, `t3_thread_launch` | Denies a local dispatch when free RAM is under a threshold (default 3 GiB). Never counts agents. |

The hooks use only the Python standard library, exit 0 on every error, and take about 20 ms.

## Install

```bash
git clone <this repo> ~/src/orchestack
~/src/orchestack/scripts/install.sh --dry-run    # shows every step, changes nothing
~/src/orchestack/scripts/install.sh
~/src/orchestack/scripts/check_drift.sh          # ok / MISSING / COPY / DRIFT / OLD per skill and root
```

`install.sh`:

1. symlinks each skill into `~/.claude/skills` and `~/.codex/skills`, so one checkout feeds both harnesses and
   `git pull` updates them (copies in two places drift apart),
2. moves any existing folder of the same name (and, with `--retire=a,b`, your own older skills of those names)
   to `~/.local/share/orchestack/backups/<timestamp>/` with `mv` (never deletes). Backups live outside the skill
   folders because agents load every `SKILL.md` they find there,
3. merges the hook registrations into `~/.claude/settings.json` after a backup, keeping every existing key,
4. adds one line to `~/.claude/CLAUDE.md` (the only text proven to survive every compaction): "Orchestrator or lead
   chats (told 'you are the orchestrator', or titled Orchestrator:/Lead:): load the `orchestrator` skill at start
   and again after every compaction."

`--watcher` (off by default) also installs the change-only watcher (see below). `--retro` (off by default)
installs the weekly retro scan (see Retro). `--uninstall` reverses all of it and restores the newest backups. `--keep-old` leaves superseded skills in place.
A Claude Code plugin manifest (`.claude-plugin/`, `hooks/hooks.json`) is included but less tested than the script.
Use one method, not both. Codex gets the skills only (the hooks are Claude Code hooks).

## Configure

Everything personal lives outside the repo, in `~/.config/orchestack/` (or `$ORCHESTACK_CONFIG_DIR`):

- `local.md`: your overlay. Every skill reads it first and it overrides their defaults. Put your name, time zone,
  usage meter command, pinned models, pace, ask threshold, weekly reset, cluster rules, orchestrator thread ids and
  standing permissions here. See `examples/local.example.md`.
- `roles.json`: `{"<T3 thread id>": "orchestrator" | "lead" | "none"}`. The role card hook reads the thread id from
  `T3CODE_THREAD_ID`. `none` silences a chat whose title would otherwise match.
- `config.json`: hook settings. See `examples/config.example.json`.
  - `memory_gate.min_available_gib` (default 3.0), `memory_gate.swap_weight` (default 0, counts that share of
    free swap as available), `memory_gate.enabled`.
  - `role_card.t3_state_db`: optional path to T3's state database. If set, a chat whose title starts with
    `Orchestrator:` or `Lead:` gets the card too. The hook opens it read-only and looks up one row by primary key.
    It never scans (the database can be tens of GB) and skips silently on any error.
  - `role_card.skill_path`: the installed orchestrator skill the card tells the chat to re-read.

## Watcher

`watcher/watch.py` replaces AI scheduled checks. It runs every 15 minutes from a systemd `--user` timer, calls no
model, and compares what it sees with its last run (`~/.local/state/orchestack/watch.json`). Each change wakes one
chat once, with a short message that starts with the reason, e.g. `watcher: jobs: train.json: all 4 jobs ended
(DONE 4). Next step: ...`. Each check is enabled by its section in `config.json` under `watcher`. Every key, default
and edge case, the job file format, the wake rules and how wakes are delivered are in
[docs/watcher.md](docs/watcher.md).

| Check | Wakes | When |
|---|---|---|
| `jobs` | the thread named in the job file | every job in the file ended or one failed, a job runs twice as long as expected or pends too long, or the owner thread was deleted |
| `children` | the child's parent | a child's latest run failed, it waits on an approval or answer, or a run is stuck |
| `disk` | the orchestrator, at once | free space on a path is below `min_free_gb` |
| `usage` | the orchestrator, with the backlog prompt | the weekly reset, usage behind pace, or less than 90 % used within 12 h of the reset, for one or several accounts |
| `decision` | the orchestrator, the parent or the child | a `decision:` sat unanswered, a child's report never reached its parent, or a `waiting:` names jobs no job file lists |
| `expiry` | the owner of the oldest open job file | a login (e.g. `tsh`) is about to expire while jobs are open (off unless configured) |
| `hosts` | nobody | a host comes back: its `on_online` command runs |

A chat writes a job file before it ends its turn on a long wait. The simplest way, from any project and without
looking up an id, is the `add` command (the thread comes from `$T3CODE_THREAD_ID`):

```bash
python3 ~/src/orchestack/watcher/watch.py add --job 4711 --job 4712 --next-step "read .work/state.md" \
  --expect-min 90 --max-pend-h 3
```

A launched lead has no lineage in T3, so its parent runs `watch.py lead --thread <lead id>` right after the launch.

```bash
watcher/watch.py --dry-run --verbose      # what it sees and would send, changes nothing
scripts/install.sh --dry-run --watcher    # then without --dry-run to enable the timer
journalctl --user -u orchestack-watch     # what it sent
```

`watch.py pace` prints, for each account in `usage`, its weekly use, where an even spread of the week (100 % over 7
days, about 14 % per day) says it should be by now, the hours to its reset and what to do. Behind pace, spend the
gap on queued work, within a day of the reset use what is left, and ahead of pace start no optional work. Orchestrators
run it before stopping work for budget, and give lanes pace conditions instead of fixed percentages. The backlog
prompt points the orchestrator at `.work/orchestrator/IDEAS.md` (format in the orchestrator skill's
`references/backlog.md`).

## Retro

Agents work around the tool errors they hit and rarely say so. `retro/scan.py` finds them without a model: it reads
the Claude Code transcripts of every `~/.claude*/projects` (skipping `api-request*` lines) and Codex sessions from
the last 7 days (also the shell and MCP results inside Codex `exec` scripts, each with its own exit code and
output), keeps the failed tool results, and leaves out the fine ones (a rejected or interrupted call, a call
cancelled after a sibling failed, exit 1 from grep, diff or test). It groups the rest by category and tool (for MCP,
server and tool), and by owner:

- a configured owner whose working directories match `retro.owners` (e.g. an app assistant's workspaces),
- "this PC" for interpreters, venvs, PATH, hooks, permission rules, missing commands and the tools in
  `retro.pc_tools`, wherever they happened,
- otherwise the git repository of the working directory.

The digest, `~/.local/state/orchestack/retro/<date>.md` (mode 0600), lists per cluster the count, the change since
the digest of the previous week (none after a gap, and an extra manual run is never the baseline) and up to 3
examples (session, transcript line, tool, the first 300 characters of the error with secrets redacted: tokens with
known prefixes, whole cookie and authorization headers, JWTs, private keys, credentials and webhooks in URLs,
password, token and key flags and values (quoted ones whole, `*_KEY=`, `*_TOKEN=`, `*_SECRET=`, `login -p`), long
hex and base64 runs). Redaction is pattern based, so keep the digest private. It never copies transcripts. One week of about 6 GB of
transcripts takes about 30 s.

```bash
retro/scan.py --days 7                    # writes today's digest and prints its path
scripts/install.sh --dry-run --retro      # then without --dry-run: Mondays 06:00, catches up after a shutdown
systemctl --user list-timers orchestack-retro.timer
```

The judge step is the `retro` skill. Schedule it yourself (e.g. a T3 scheduled task on Monday morning that says
"retro"). It reads the digest, opens a transcript only at a cited line, and sends each owner at most 3 proposals:
the tool, its description or its error message first, then this PC's setup, then a skill, and CLAUDE.md or
AGENTS.md last. It changes nothing itself.

## Development

```bash
git config core.hooksPath .githooks   # pre-commit runs the scrub check and lint
scripts/lint.sh                        # frontmatter, size budgets, JSON, bash -n, Python syntax, prose rules
scripts/scrub_check.sh                 # no personal data in the repo
scripts/scrub_check.sh --unpushed      # also in the lines that unpushed commits added: run it before a push
python3 -m pytest tests                # or: uvx pytest tests
```

Put your own names, hosts, project names (private repositories too) and ids in the git-ignored
`.scrub-patterns.local` (one regex per line). Write test fixtures with invented names, never ones copied from a
transcript.

## Status

Version 0.1. The hooks are unit-tested and were run against a real T3 state database. The role card firing after
compaction under T3's resume path is not yet proven in a live chat. Pilot it on one lead first.

## Licence and credits

[MIT](LICENSE). `skills/sol-loop` is adapted from Theo Browne's `sol-loop` in
[tstack](https://github.com/t3dotgg/tstack) (MIT). His notice is kept in `skills/sol-loop/NOTICE.md`. Other ideas
are credited in [CREDITS.md](CREDITS.md).
