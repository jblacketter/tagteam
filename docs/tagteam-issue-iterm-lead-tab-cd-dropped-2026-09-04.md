# Tagteam issue note: iTerm2 Lead tab starts in `~` because the injected `cd` is eaten by shell-startup output

- **Observed:** 2026-09-04, sonicgrid (`~/projects/sonic/sonicgrid`), tagteam CLI 3.11.0, iTerm2 backend. Reported by the arbiter ("when I try and start a handoff it is defaulting to my user root"); reproduced on demand by Claude from the project directory.
- **Status:** Reproduced, root-caused, not fixed in tagteam. A dotfile workaround is recommended below (not yet applied as of 2026-09-04). Backlog candidate — promote to a phase when the iTerm2 backend is next touched.

## Observed behavior

`tagteam session start` (iTerm2 backend) opened three tabs. The Watcher and
Reviewer tabs were in the project directory; the **Lead tab stayed in
`/Users/jackblacketter`**, and `claude` launched there and showed the
"Accessing workspace: /Users/jackblacketter" trust prompt. The project's
`.handoff-session.json` was correct (`project_dir` pointed at sonicgrid), so
the state layer was fine — only the first tab's shell was in the wrong place.

Screen text of the Lead tab, captured with `contents of session`:

```
Last login: Fri Sep  4 17:05:24 on ttys002
cd /Users/jackblacketter/projects/sonic/sonicgrid
[oh-my-zsh] Would you like to update? [Y/n]
[oh-my-zsh] You can update manually by running `omz update`
d /Users/jackblacketter/projects/sonic/sonicgrid
claude
 jackblacketter@Jacks-Mac-mini  ~  d /Users/jackblacketter/projects/sonic/sonicgrid
 jackblacketter@Jacks-Mac-mini  ~  claude
 Accessing workspace:
 /Users/jackblacketter
```

## Root cause

`iterm.py::create_session` does `write text "cd <abs_dir>"` immediately after
creating each tab, before the shell has reached its prompt. The typed text sits
in the tty input buffer. If shell startup **reads from the tty** before the
prompt appears, it consumes the head of that buffer. Here the oh-my-zsh
auto-update check (`[Y/n]` read) swallowed the `c` of `cd` as its answer; the
remainder ran as the nonexistent command `d /Users/...`, so the tab never left
the profile's default working directory (iTerm2 "Working Directory" = home).
The launch command (`claude`) that followed was typed correctly, so Claude Code
started in `~`.

Only the Lead tab was affected in this run because it is the freshly created
window's first session — the slowest to reach a prompt — but any tab can hit it
whenever startup output includes a read (omz update prompt, a `read -q` in
`.zshrc`, an interactive tool-version manager, a "Last login" MOTD that pauses).
It is a race, so it presents intermittently and looks like tagteam "picking the
wrong project".

The tmux backend is not affected: `create_tmux_session` starts the session with
`-c <start_dir>`, so no `cd` is typed at all.

## Expected behavior

Every tab created by `tagteam session start` is in `project_dir` before any
agent launch command or prime message is sent, regardless of what the user's
shell does during startup.

## Suggested fix

1. **Wait for the shell prompt before writing the `cd`.** `session.py` already
   has `wait_for_agent_ready()` (polls the tab tail for a prompt pattern before
   priming, added precisely because a fixed sleep raced Claude Code's startup).
   Apply the same readiness gate to the shell: poll `get_session_contents()`
   for a shell-prompt pattern (or an empty tail line ending in `$`/`%`/`❯`),
   then write `cd`, then write the launch command.
2. **Verify the cwd after the `cd`** using iTerm2's `variable named
   "session.path"` (AppleScript) and retry once or fail loudly with the tab's
   last lines. A wrong-cwd tab should be a session-start error, not a silent
   "No active cycle" from the lead a minute later.
3. **Cheaper alternative if (1) is too fiddly:** set the working directory on
   the profile at creation time — `create tab with profile` cannot take a
   directory, but `write text` can be replaced by launching the shell with
   `command` in the AppleScript (`create tab with default profile command "…"`)
   or by `cd` + launch in a single line so a swallowed first character breaks
   the whole line visibly rather than half of it.
4. Regression test: a fake `get_session_contents` that returns a `[Y/n]` line
   for the first N polls and a prompt afterwards; assert the `cd` is not written
   until the prompt appears.

## Workaround (recommended for the arbiter's machine; not yet applied 2026-09-04)

Disable the interactive oh-my-zsh update prompt so nothing reads from the tty
during startup — in `~/.zshrc`, above `source $ZSH/oh-my-zsh.sh`:

```zsh
zstyle ':omz:update' mode reminder
```

(`auto` or `disabled` also work.) After that, `session start` puts all three
tabs in the project. This only covers oh-my-zsh; any other startup read
reintroduces the race, which is why the fix belongs in the backend.

## Diagnostics that found it

- `.handoff-session.json` had the right `project_dir`, and `tagteam hook
  session-start` from the project printed the right phase — so the state layer
  was fine.
- Per-tab cwd via AppleScript: `variable s named "session.path"` for each
  session id in `.handoff-session.json`.
- Per-tab screen via `contents of s` showed the swallowed `c`.
