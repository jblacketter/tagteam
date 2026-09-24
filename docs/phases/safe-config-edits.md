# Phase 71b: Safe config edits

## Status
- [ ] Planning: plan cycle open (round 1)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
Phase 71's Rules tab shows the `tagteam.yaml` rules read-only and names the
key to change. This phase makes a small, safe set of them editable. The CLI
comes first, then the Rules tab. Every edit shows a diff before it is written.

Edits are **targeted line edits**: a PyYAML load-and-dump would drop the
file's comments, blank lines and key order. Before anything is written, each
edit is **validated by parsing the edited text and running the same resolver
the engine uses**, and the edit is refused unless the engine would then do
what was asked.

## Scope
**In**
- New module `tagteam/config_edit.py`: locate, edit, validate, write.
- New CLI `tagteam config set KEY VALUE [--preview]` and
  `tagteam config keys`.
- Cockpit: `POST /api/config/set` (the action pattern, plus a preview that
  returns the diff), and controls on the editable Rules-tab rows.
- Tests.

**The safe set (closed list):**

| Key | Value | Resolver that must agree afterwards |
|---|---|---|
| `gatekeeper.enabled` | `true` / `false` | `gatekeeper.resolve_gatekeeper(...).enabled` |
| `gatekeeper.on_submit` | `true` / `false` | `resolve_gatekeeper(...).on_submit` |
| `panel.enabled` | `true` / `false` | `panel.resolve_panel(...).enabled` |
| `briefer.enabled` | `true` / `false` | `briefer.resolve_briefer(...).enabled` |
| `watcher.resend_minutes` | integer ≥ 0 | `config.resolve_watcher(...)[0]` |

**Out**
- Any other key: agent names, commands, test commands, lens lists,
  providers. These are edited by hand. `config keys` lists the safe set, and
  the refusal for any other key says to edit the file by hand.
- Creating `tagteam.yaml`.
- Rewriting or normalising anything the edit does not touch.

## Technical approach

### The edit: a small line-based locator, deliberately narrow
It works on the file's text, split into lines, keeping each line's own line
ending (`\n` or `\r\n`) and whether the file ended with a newline.

- **The block:** exactly one top-level line `^<block>:\s*(#.*)?$` (indent 0).
  - If it is absent, the edit **appends** a new block at the end of the file:
    `<block>:` + `  <key>: <value>`, with one blank line before it when the
    file does not already end with one. The child indent is 2 spaces.
  - More than one such line, or a top-level `<block>: <something>` (flow
    style `{…}`, a scalar, an anchor `&`, or an alias `*`), is **refused**:
    "edit `tagteam.yaml` by hand".
- **The block's body:** every following line until the next line that is
  non-blank, not a comment, and indented at 0. The child indent is the indent
  of the first non-blank, non-comment body line.
- **The key:** exactly one body line at the child indent that matches
  `^(\s*)<key>:(\s*)(<scalar>)(\s*#.*)?$`, where the scalar is a plain token
  or a quoted string.
  - When it is present, **only the scalar is replaced**. The indent, the
    spacing around it and a trailing comment are kept byte for byte.
  - When it is absent, a line `<indent><key>: <value>` is **inserted** right
    after the block header, at the child indent.
  - Two matching lines, a value that continues onto another line (`|`, `>`,
    or an empty value followed by deeper-indented lines), or a tab anywhere
    in the block's indentation is **refused**.
- **Values are written canonically:** `true` / `false`, and integers in
  decimal.

### Validation: the engine must agree, or nothing is written
1. **The new text parses** with `yaml.safe_load` into a dict.
2. **Nothing but the target changed:** the old and new parsed configs are
   equal apart from the target key, using a deep compare with the target
   removed from both. This guards the locator itself: an edit that changed
   anything else is refused as an internal error, and nothing is written.
3. **The engine does what was asked:** the resolver in the table above,
   run on the new config, yields the requested value.
   - If it does not, the edit is refused with the resolver's own problems,
     for example: "`panel.enabled: true` is refused: the panel would still
     be OFF — panel: the reviewer must validate for headless turns".
   - A request that is already in effect is a no-op: "already `true`;
     nothing written" (exit 0).
   - Turning a feature **off** is always allowed, even when its block has
     other problems. Off cannot fail, and it is the way out of a broken
     configuration.
4. **Problems elsewhere:** `validate_config` problems in *other* blocks
   that were already there do not block the edit, and are reported as notes.
   A problem the edit would *introduce* in another block is impossible by
   step 2.

### Writing: the same guarded write as the orders file
- Refused under `TAGTEAM_READ_ONLY` before anything is read for writing.
- `tagteam.yaml` must be a regular file, and no path component may be a
  symlink (`safe_read._lstat_chain`).
- It is read with `read_bounded` (256 KB cap). The write goes to a temp file
  with `O_CREAT | O_EXCL | O_NOFOLLOW`, keeps the original file mode, then
  `os.replace`.
- **Compare-and-swap:** immediately before the replace, the file is read
  again. If it no longer matches the bytes the edit was computed from, the
  edit is refused ("tagteam.yaml changed while editing; nothing written").
  Otherwise a hand edit could be silently overwritten.
- It is never created: a missing `tagteam.yaml` is refused ("run tagteam
  init").

### CLI
```
tagteam config keys                     # the safe set, each with its current resolved value
tagteam config set KEY VALUE --preview  # unified diff + what the engine will do; writes nothing
tagteam config set KEY VALUE [--by NAME]
```
- Output is the unified diff (`--- tagteam.yaml` / `+++ tagteam.yaml
  (edited)`), then one line saying what the engine will do, for example
  "gate: ON · runs at submission".
- If a watcher is running (the same heartbeat check as the Rules tab), a
  line says that it keeps its current settings until restarted.
- `--preview` and `keys` are reads, so both go into `READ_ONLY_COMMANDS`.
  `set` without `--preview` is refused under read-only.
- Exit codes: 0 (written, or no-op), 1 (refused), 2 (usage).

### Cockpit
- `POST /api/config/set`, body `{key, value}`, uses the existing action
  pattern (`_plan` → `config_edit.config_command`, `--by` web user,
  `{ok, message, cli}`).
  - `{preview: true}` runs the `--preview` form and returns its output (the
    diff). It is a read, so it is allowed while the page is read-only.
  - `dry_run` returns the CLI line.
  - Keys outside the safe set, and wrong value types, get a 400 before
    anything runs. Booleans must be JSON booleans, and `resend_minutes` a
    non-negative JSON integer, matching the Phase 71 r1 lesson on `run` and
    `id`.
- **The Rules tab:**
  - The gate, panel and briefer rows get an **On/Off** switch.
  - The gate row also gets **"at submission" / "in the watcher"**
    (`on_submit`).
  - The re-send row gets a number field with **Save**.
  - Each change first fetches the preview and shows it in the confirm modal:
    the diff, what the engine will do, and the exact CLI line.
  - A refused edit shows the refusal reason. Nothing changes and the control
    returns to the saved state.
  - The payload marks which rows are editable (`edit: {key, kind}`), so the
    page does not hard-code the safe set. The "to change: … in tagteam.yaml"
    hint stays for the rows that are not editable.
- After a write, the Rules tab reloads, and the Phase 71 "running watcher
  still uses its earlier settings" notice appears when a watcher is running.

### Scope-check interaction (stated, not changed)
`tagteam.yaml` is **not** added to the impl-scope exclusions. A phase may
legitimately change the project's config, and the gate must see that. An
arbiter who edits config mid-cycle is making a visible change, and the
submission should mention it.

## Files
- `tagteam/config_edit.py` (new): `plan_edit(text, key, value) ->
  Edit | Refusal`, `apply(root, key, value, preview)`, `config_command`,
  `SAFE_KEYS`.
- `tagteam/cli.py`: dispatch `config`, `READ_ONLY_COMMANDS`, help.
- `tagteam/cockpit_api.py`: `_plan("config/set")`, the preview path, and
  `edit` markers in `rules_payload`.
- `tagteam/server.py`: the route.
- `cockpit.js` / `.css`: a "Phase 71b" slice for the row controls.
- Docs: README / how-tagteam-works (`tagteam config`), and the workflows
  Steering list.
- Tests: `tests/test_config_edit.py` (new) and additions to
  `tests/test_cockpit_rules.py`.

## Success criteria
1. **Every safe key, edited in a realistic `tagteam.yaml`** (comments above
   and inside blocks, blank lines, a trailing comment on the edited line,
   quoted values elsewhere, CRLF, no trailing newline): the diff touches
   exactly the target line (or inserts one line, or appends one block), and
   every other byte is identical.
2. **Refused, with nothing written:**
   - flow-style or scalar block;
   - duplicate block or duplicate key;
   - a block scalar value;
   - a tab in the indentation;
   - an anchor or alias;
   - a key outside the safe set;
   - a wrong value type;
   - a missing `tagteam.yaml`;
   - a symlinked `tagteam.yaml`;
   - read-only mode;
   - the file changed between compute and write (compare-and-swap);
   - an edit whose resolver would not yield the requested value (enabling
     the panel without a headless reviewer, enabling the briefer without a
     provider), with the resolver's reason in the message.
3. **Turning a feature off always succeeds**, including in a block with
   other problems. After it, the resolver reads OFF.
4. **After every successful edit**, re-reading the file with the engine's
   resolver gives the requested value. The parsed config differs from the
   old one only at the target key (step 2, pinned by a property-style test
   over several file shapes).
5. **Previews and `keys`** write nothing (the tree's bytes are unchanged)
   and work under read-only.
6. **The cockpit:**
   - a switch shows the diff and CLI line before writing;
   - a refused edit reports the reason and leaves the control on the saved
     state;
   - bad params get a 400 with no mutation;
   - rows that are not editable keep their hint.

   Driven in a real page, with `tagteam config keys` agreeing afterwards and
   the stale-watcher notice showing when a watcher runs.
7. The existing Rules, cockpit and config tests pass. There is no change to
   how the engine reads `tagteam.yaml`.

## Risks and open questions for the reviewer
- **A narrow locator by design.** Anything unusual is refused rather than
  guessed at, and the parsed-equality check (validation step 2) backs up the
  locator: even a locator bug cannot write a config that differs anywhere
  but the target.
- **Refusing an enable the engine would not honour.** I think this is right,
  because writing `enabled: true` for something that stays OFF is the exact
  confusion Phase 71 fixed. The alternative is to write it with a warning.
- `tagteam.yaml` stays in the scope check (see above).
