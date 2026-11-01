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

## What is in it

| Piece | Say or trigger | What it does |
|---|---|---|
| `skills/orchestrator` | "you are the orchestrator", "orchestrate this", "work autonomously", "decide for me", "parallelize", "I'm going to sleep", chats titled `Orchestrator:` or `Lead:` | The playbook: parallel lanes via delegates, decide reversible steps, no "want me to...?", pilot pricing, pace, waits, briefs, models. |
| `skills/sol-loop` | "sol loop", "dual critic", "critic loop" | One review loop with two independent critics per round (GPT Sol + a fresh Opus), smallest fixes, a ledger, a stop rule. |
| `skills/bulk-image-review` | before agents look at many images | Cost model, ask-first threshold, and the small-fresh-worker pattern. |
| `hooks/role_card.py` | Claude Code `SessionStart` (`startup`, `resume`, `compact`) | Prints a 7-line role card into context for orchestrator and lead chats, so the role survives compaction. |
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

`--uninstall` reverses all four and restores the newest backups. `--keep-old` leaves superseded skills in place.
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

## Development

```bash
git config core.hooksPath .githooks   # pre-commit runs the scrub check and lint
scripts/lint.sh                        # frontmatter, size budgets, JSON, bash -n, Python syntax, prose rules
scripts/scrub_check.sh                 # no personal data in the repo
python3 -m pytest tests                # or: uvx pytest tests
```

Put your own names, hosts, project names and ids in the git-ignored `.scrub-patterns.local` (one regex per line).

## Status

Version 0.1. The hooks are unit-tested and were run against a real T3 state database. The role card firing after
compaction under T3's resume path is not yet proven in a live chat. Pilot it on one lead first.

## Licence and credits

[MIT](LICENSE). `skills/sol-loop` is adapted from Theo Browne's `sol-loop` in
[tstack](https://github.com/t3dotgg/tstack) (MIT). His notice is kept in `skills/sol-loop/NOTICE.md`. Other ideas
are credited in [CREDITS.md](CREDITS.md).
