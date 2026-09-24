#!/bin/sh
# Phase 73: the read-only guard for the tagteam kit agents' Bash (PreToolUse).
#
# Every Bash call in a session with the plugin passes through here, so the
# common case must be cheap: a payload that does not mention a tagteam: agent
# exits 0 at once, with no output and no Python start.
#
# For a payload that does, this is the FAIL-CLOSED layer. The guard's exit code
# is its verdict (tagteam hook pre-tool-use):
#   0  kit agent, rewritten   -> forwarded only after plugin-side structural
#                                validation (validate_guard.py); else blocked
#   3  verified: not a kit agent (a prefilter false positive) -> pass untouched
#   2  guard deny (unusable input) -> blocked, with the guard's reason
#   *  no CLI (127), an older CLI without the subcommand (1), a crash -> blocked
# Exit 2 is Claude Code's blocking exit; its stderr goes to the agent.
input=$(cat)
case "$input" in
  *'"agent_type"'*'tagteam:'*) ;;
  *) exit 0 ;;
esac
need="tagteam kit agents run Bash only through tagteam's read-only guard"
if ! command -v tagteam >/dev/null 2>&1; then
  echo "$need: the tagteam CLI is not on PATH (install: uv tool install tagteam)" >&2
  exit 2
fi
out=$(printf '%s' "$input" | tagteam hook pre-tool-use)
rc=$?
case $rc in
  3) exit 0 ;;
  0)
    if ! command -v python3 >/dev/null 2>&1; then
      echo "$need: python3 is needed to validate the guard's rewrite" >&2
      exit 2
    fi
    valid=$(printf '%s' "$out" | TAGTEAM_HOOK_INPUT="$input" python3 "$(dirname "$0")/validate_guard.py") || exit 2
    printf '%s\n' "$valid"
    exit 0
    ;;
  2) exit 2 ;;
  *)
    echo "$need: this tagteam CLI has no working 'tagteam hook pre-tool-use' (exit $rc) — upgrade it: uv tool upgrade tagteam" >&2
    exit 2
    ;;
esac
