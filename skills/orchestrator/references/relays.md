# T3 relays and the incidents behind the reporting and waiting rules

The orchestrator skill states the rules. This file holds how T3 relays turn ends, what is only observed, and the
incidents that taught each rule, so the skill can stay short.

## Which turn ends T3 relays to the parent

- **Relayed, whatever the turn says:** a turn of an async delegate (`delegate_task`) that its task, its parent's
  message or a background command started. No setting turns this off. A relay is a new message in the parent, so
  it wakes the parent.
- **Not relayed:** a turn the watcher started (an outside `t3 thread send`). This is why the watcher's jobs wake
  tells a child owner to send its report with `t3_thread_send`.
- **Not relayed, seen once:** the turns that the completion notices of a delegate's own delegates started (four
  critic turns in one case). The skill treats these as not relayed.
- **Not verified:** a turn a scheduled wake started. The skill sends from it to be safe.
- **Launched leads** (`t3_thread_launch`) have no completion relay at all. A lead wakes its parent only when it
  sends, and its own delegates' relays wake the lead, not the parent.
- `t3_background_wait` does not block. It delays the current turn's relay until the command ends, so the parent
  gets two relays close together.

## Incidents

| Rule | What happened |
|---|---|
| Leads forward deliverables in the same turn | Two leads sat on finished review pages until the user found them. |
| A child waits on its own critics inside the turn, sends from unrelayed turns, and starts every report with its word | A delegate ended its turns to wait for its `sol-loop` critics. T3 did not relay the four turns their notices started, and its final report lacked `done:`, so the result sat unread for 2.2 h. |
| Never re-arm a background loop | One child re-armed its loop and woke its orchestrator 12 times overnight. |
| After a move or restart, confirm every waiting child has a live wake | A child that was "only waiting on the cluster" lost its wake, and its result sat unread for 6.6 h. |
| Launch long lanes as leads | A 6-day lead ran 293 turns and woke its orchestrator 76 times. As a delegate it could have woken it up to 293 times. The cost moves to the lead's sends: the same lead sent six messages in its first hour. |
| Copy the brief blocks word for word | An outdated copy of the rules pasted into a brief caused about 25 extra parent wakes. Reworded lines such as "background waits as a shell loop" or "send `waiting:` up" cost wakes the same way. |
