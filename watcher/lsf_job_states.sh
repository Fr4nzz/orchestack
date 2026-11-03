#!/usr/bin/env bash
# orchestack watcher: how LSF jobs that dropped out of the bjobs listing ended. Runs ON the cluster: feed it to a
# login shell there with the job ids as arguments, e.g. as the watcher's jobs.lookup_command:
#   "ssh cluster-login \"bash -l -s -- {jobs}\" < /path/to/orchestack/watcher/lsf_job_states.sh"
# Prints "<jobid> <state>" per job it finds (DONE or EXIT, or the bjobs state), nothing for a job it cannot find.
# Order: bjobs -a (recent jobs), then bacct -l (the current accounting file), then bhist -l over the last
# ORCHESTACK_BHIST_FILES (10) event files, because busy clusters rotate their logs within hours. Read-only.
set -u
ids=()
for j in "$@"; do
  [[ $j =~ ^[0-9]+(\[[0-9]+\])?$ ]] && ids+=("$j")
done
[ ${#ids[@]} -gt 0 ] || exit 0
declare -A found=()

# Long-format LSF output wraps at 80 columns with 21-space continuation lines: join them, then report the last
# terminal event per "Job <id>" block.
ended() {
  awk '
    function flush() {
      if (buf ~ /^Job <[0-9\[\]]+>/) { id = buf; sub(/^Job </, "", id); sub(/>.*/, "", id) }
      else if (id != "" && buf ~ /Done successfully|Completed <done>/) st[id] = "DONE"
      else if (id != "" && buf ~ /Exited|Completed <exit>/) st[id] = "EXIT"
    }
    /^                     / { sub(/^ +/, ""); buf = buf $0; next }
    { flush(); buf = $0 }
    END { flush(); for (k in st) print k, st[k] }'
}

record() {  # $1 = all: print every line (bjobs lists each array element under the base id), else the first per id
  local id st
  while read -r id st _; do
    [[ $id =~ ^[0-9]+(\[[0-9]+\])?$ ]] && [ -n "$st" ] || continue
    [ "${1:-}" = all ] || [ -z "${found[$id]:-}" ] || continue
    found[$id]=$st
    echo "$id $st"
  done
}

rest() {
  local j
  for j in "${ids[@]}"; do [ -z "${found[$j]:-}" ] && echo "$j"; done
}

record all < <(bjobs -a -o "jobid stat" -noheader "${ids[@]}" 2>/dev/null)
mapfile -t left < <(rest)
[ ${#left[@]} -gt 0 ] && record < <(bacct -l "${left[@]}" 2>/dev/null | ended)
mapfile -t left < <(rest)
[ ${#left[@]} -gt 0 ] && record < <(bhist -n "${ORCHESTACK_BHIST_FILES:-10}" -l "${left[@]}" 2>/dev/null | ended)
exit 0
