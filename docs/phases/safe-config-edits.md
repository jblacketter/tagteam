# Phase 71b: Safe config edits

## Status
- [x] Planning: approved round 2 (2026-09-23) at `3b496be`. r1: saved vs effective, preview-bound writes, one writer lock, expected-tree check.
- [x] Implementation: branch `phase-71b-safe-config-edits`
- [x] Implementation Review: approved round 2 (2026-09-23) at `f23224a`; gate 2,469 passed, 5 skipped
- [ ] Complete: PR #58 open, awaiting the arbiter's merge.

## Closeout
```
Phase report: safe-config-edits — plan approved r2 · impl approved r2
  plan   2 rounds · 1 change request · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 16m 15s
  time   start→approve 1h 18m · implementation before first submit 51m 56s
         lead 4m 15s (2 spans, 2 unknown) · reviewer 5m 46s (4 spans) · gate 16m 16s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 8 · no token data 0 · unmatched 6 · unknown 2
```
- **Plan r1:** no-ops were decided on effective values, not saved ones. A confirmed write was not bound to the bytes it previewed. The re-read before `os.replace` was not a real compare-and-swap. The expected-tree check broke on an appended block.
- **Impl r1:**
  - quoted or duplicate spellings bypassed the text locator;
  - the page offered no explicit OFF for an absent or invalid boolean;
  - a pending minutes edit was lost on refresh;
  - the diff was unreadable when the file had no final newline.

  Every item has a regression test.

## Implementation notes
**Found by testing:** in the first cut, the no-op check ran *before* the layout refusals. A duplicated key or an alias that parses to the requested value slipped through as "already set", without the layout ever being checked. The locator now always runs first, so layout refusals apply even to a no-op.

**Found by looking** (a scratch project, `tagteam serve --theme cockpit`, Playwright MCP): a toggle labelled "Gate: Off" reads two ways, as a state or as an action. Each boolean now shows its saved state in words ("Gate — saved: off") next to an explicit **Turn on / Turn off** button.

**Driven in the page:**
- the preview modal showed the diff, what the engine will do, and the CLI line with `--expect`;
- an external edit while the modal was open meant the confirm was refused with a 409, the file kept the external edit byte for byte, and there was one POST and no retry;
- a fresh attempt wrote exactly one line, and `tagteam config keys` agreed;
- `resend_minutes: 5` in an invalid watcher block was refused with the resolver's reason, and the field returned to the saved 3.

**Impl r1 review (all four reproduced and fixed):**
1. **Quoted and duplicate spellings.** `safe_load` collapses duplicate keys and hides quoting, so the text-only locator could append a second `gatekeeper` block beside `"gatekeeper": {…}`. The locator is now checked against `yaml.compose`, whose node tree keeps every key with its quoting style and line. A quoted target name, or a duplicated block or leaf, is refused. The locator's header and key lines must match the parser's lines.
2. **An explicit OFF from the page.** An absent or invalid saved boolean now offers both **Set on** and **Set off**. Before, the only action was the enable, so an invalid `briefer.enabled: "true"` whose enable is refused could not be persisted as a real `false`.
3. **A pending minutes edit.** The pending text is now kept per key and survives refreshes. It is cleared when its write completes, on a refusal, or on a no-op. The field's focus is restored after the rows are back in the page.
4. **The diff when the file has no final newline.** The diff is now rendered with separate `-` / `+` lines and git's `\ No newline at end of file` marker; before, the removed and added lines ran together on one line.

**Deviations from the plan text:**
- A YAML file indented with tabs does not parse at all, so it is refused as "does not parse — fix it by hand" before the tab check, which remains as a defence.
- The Chromium test harness now awaits an optional `DONE` promise, with `--virtual-time-budget`, so it can drive the asynchronous preview-then-confirm flow.

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

### Saved versus effective (plan r1 review)
Every decision about *what to write* uses the **saved** value: the target key
as it is typed in the parsed file. It is `absent`, `invalid` (present but the
wrong type, e.g. `enabled: "true"`), or `set` (a real bool, or an int ≥ 0).
The **effective** value is what the engine's resolver yields, and it is
checked separately, after the edit.

- **A no-op** happens only when the saved value is `set` and equal to the
  requested value, with the right type. "already `false`; nothing written",
  exit 0. An effective OFF never makes a disable a no-op. For example, with
  `gatekeeper.enabled: true` and an invalid `scope`, the gate resolves OFF,
  but `set gatekeeper.enabled false` still **writes** `false`. Repairing
  `scope` later then leaves the gate OFF, as the arbiter asked. The same goes
  for `watcher.resend_minutes: 3` in an invalid watcher block that resolves
  to 15: setting it to 3 is a no-op on the saved value, and the output says
  the block's other problem still keeps the effective value at 15.
- **The payload and the controls use saved values.** Each editable row in
  `/api/rules` carries
  `edit: {key, kind: "bool"|"minutes", saved, saved_state: "absent"|"invalid"|"set"}`
  beside its effective `on`/`value`. The switch shows the **saved** value.
  When saved and effective differ, the row says "saved: on · not in effect:
  <the resolver's reason>". So the arbiter can persist OFF even when an
  invalid block already resolves OFF.

### Validation: nothing is written unless all of this holds
1. **The new text parses** with `yaml.safe_load` into a dict.
2. **Nothing but the target changed, compared against an expected tree:**
   `expected = deepcopy(old)`, then set `expected[block][key] = value`.
   **Only the target's own parent** is normalised: when the block was
   absent, or present but empty (`watcher:` parses to `None`), it becomes
   `{}` before the key is set. No other empty mapping is touched. The check
   is `new == expected`, which covers an absent block, an empty block, an
   absent key and a present key. A mismatch means a locator bug: the edit is
   refused as an internal error, and nothing is written.
3. **The engine honours the request.** The resolver in the table above, run
   on the new config, must yield the requested value for an **enable** (a
   boolean set to `true`) and for **`resend_minutes`**. Otherwise the edit is
   refused with the resolver's own problems, for example "the panel would
   still be OFF: the reviewer must validate for headless turns". A
   **disable** needs no resolver agreement: after `enabled: false` the
   resolvers read OFF by construction. It is still subject to every file
   and layout refusal (a flow-style block, a symlink, read-only mode, …).
   "Off always succeeds" means "off is never refused *by the engine check*".
4. **Other problems:** existing `validate_config` problems in other blocks do
   not block the edit, and are reported as notes. Step 2 makes it impossible
   for the edit to introduce a new one.

### Writing: serialized, bound to what was previewed
- **Refused under `TAGTEAM_READ_ONLY`** before anything else happens.
- **Guarded file access:** `tagteam.yaml` must be a regular file, and no
  path component may be a symlink (`safe_read._lstat_chain`). It is read with
  `read_bounded` (256 KB). The write goes to a temp file with
  `O_CREAT | O_EXCL | O_NOFOLLOW`, keeps the original mode, then
  `os.replace`. `tagteam.yaml` is never created.
- **One lock for all config writers** (plan r1 review):
  - The whole critical section runs under the project's
    `dualwrite.writer_lock(root)`: read, locate, validate, final re-read,
    replace. That lock is a per-project thread RLock plus `fcntl.flock`
    across processes, the same one every cycle/state write takes.
  - Two tagteam writers (CLI, cockpit, or both) therefore never interleave.
    The second reads the first one's result and applies its own edit on top,
    so both edits survive.
  - Previews and `keys` take no lock.
- **The final re-read, stated honestly:** just before the replace, still
  under the lock, the file is read again, and a mismatch with the bytes the
  edit was computed from is refused. That catches an external editor that
  wrote *before* this point. An uncooperative external tool that writes
  between that re-read and `os.replace` can still lose its write, because
  nothing short of that tool also taking the lock prevents it. The docs say
  so.
- **Bound to the preview** (plan r1 review):
  - A preview reports `base`, the sha256 of the exact bytes it diffed.
  - `config set … --expect SHA` refuses unless the file's bytes still hash to
    `SHA` when the lock is taken: "tagteam.yaml changed since the preview —
    preview again; nothing written" (exit 1; HTTP 409).
  - The cockpit always sends the `base` it showed. On that refusal the page
    reports it, **does not retry**, and fetches a fresh preview only when the
    arbiter acts again.
  - The CLI without `--expect` computes and writes in one locked step. That
    is its own preview-free path.

### CLI
```
tagteam config keys                     # the safe set, each with its current resolved value
tagteam config set KEY VALUE --preview  # unified diff + what the engine will do + `base: <sha256>`; writes nothing
tagteam config set KEY VALUE [--expect SHA] [--by NAME]
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
- `POST /api/config/set`, body `{key, value, expect}` (`expect` is the preview's `base`, required on a confirmed write from the page), uses the existing action
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
3. **Turning a feature off is never refused by the engine check**, including
   in a block with other problems. After it, the resolver reads OFF. File and
   layout refusals still apply, and are tested separately.
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
8. **Saved versus effective** (r1 review):
   (a) `gatekeeper.enabled: true` with an invalid `scope`: effective OFF,
       saved `true`, and the row shows "saved: on · not in effect: …";
       `set gatekeeper.enabled false` **writes** `false`, not a no-op; then
       repairing `scope` by hand leaves the gate OFF.
   (b) `watcher.resend_minutes: 3` in a block with an unknown key: effective
       15, saved 3; `set … 3` is a no-op that names the other problem;
       `set … 5` is refused because the engine would still use 15; after
       removing the bad key by hand, `set … 5` writes it and the resolver
       reads 5.
   (c) `panel.enabled: "true"` (invalid saved value): `set panel.enabled
       false` writes a real `false`.
9. **Absent and empty target blocks:** with `watcher` absent, or present as
   an empty `watcher:`, setting `resend_minutes` writes it. The expected-tree
   check passes, and no other empty mapping in the file is normalised
   (there is a fixture with an unrelated empty block).
10. **Bound to the preview:** preview → an external edit of `tagteam.yaml` →
    confirm with the old `base` is refused (409), the file keeps the
    external edit byte for byte, and the page makes no second POST. This is
    tested at the API level and in real Chromium.
11. **Serialized writers:** a deterministic interleaving test. Writer A
    pauses inside the critical section (a test hook); writer B, editing a
    different safe key, is started and verified to be blocked; A is released;
    both edits are in the final file. It runs with two threads and again with
    two processes, since the lock is `flock` across processes.

## Risks and open questions for the reviewer
- **A narrow locator by design.** Anything unusual is refused rather than
  guessed at, and the parsed-equality check (validation step 2) backs up the
  locator: even a locator bug cannot write a config that differs anywhere
  but the target.
- **Refusing an enable the engine would not honour.** The reviewer agreed in
  r1.
- **The external-editor race** is narrowed, not closed (see Writing). Only
  the lock closes it, and a text editor does not take it.
- `tagteam.yaml` stays in the scope check (see above).
