# Codex PR-review prompt template

Skeleton for sending Codex at a PR. Copy this file, fill in the
`{{PLACEHOLDERS}}`, paste the result into Codex.

The structural sections (deliverable format, "what not to comment on")
are stable across reviews. The context section, focus areas, and
verdict definitions change per round.

Authoring notes (delete before sending):
- **Force a position**, don't let Codex punt. "Take an explicit
  position" + "do not punt with depends" produces real pushback.
- **Specific over general.** "Verify the index `(cycle_id, round, id)`
  is right for the ORDER BY r.id queries" beats "review the schema."
  Names of files, sections, line numbers if you can.
- **Cap the surface.** A 12-item focus list buries the items that
  matter. 5–6 specific architectural questions + a "gaps to look
  for" list usually fits.
- **"What not to comment on"** does real work — without it Codex
  expands into surface-level critique. Always include it.
- **Verdict labels** match the PR's stage: design docs use
  `{ready-to-implement, more-changes, blocked}`; implementation PRs
  use `{merge, changes-requested, blocked}`.

---

## Prompt body (everything below is what you actually send)

You are reviewing PR #{{PR_NUMBER}} on jblacketter/tagteam:
https://github.com/jblacketter/tagteam/pull/{{PR_NUMBER}}

Branch: `{{BRANCH_NAME}}`

{{ONE_PARAGRAPH_CONTEXT — what this PR does, what changed since
your previous review (if applicable), what's deliberately not in
scope. Name commits or sections so Codex finds the right code
quickly.}}

CONTEXT YOU MAY WANT FIRST:

{{LIST 1–3 things to read before reviewing — design doc, predecessor
PR, specific source files. Skip this section for small PRs.}}

WHERE TO FOCUS:

{{NUMBERED LIST of 4–6 specific architectural questions. For each:
- Name the file/function/section
- State the design decision being challenged
- Suggest counter-arguments Codex should engage with
- For open questions in the design, ASK Codex to take a position.}}

GAPS TO LOOK FOR:

{{BULLETED LIST of things that might be missing — failure modes
the doc didn't address, code paths the tests don't cover, edge
cases the implementation glossed over. Brief is fine; listing the
gap is more valuable than fully proposing the fix.}}

WHAT NOT TO COMMENT ON:

- Style / formatting / typos.
- Section ordering decisions.
- Items resolved cleanly in earlier review rounds (don't re-litigate).
- Out-of-scope items the PR description explicitly defers.
- The decision to do this work at all.

DELIVERABLE:

Structured review:

**Verdict:** one of {{VERDICT_LABELS}}. One sentence on why.

**{{BLOCKER_OR_CHANGES_HEADING}}** (if any): bulleted list. For each:
file:line, severity (blocker / should-fix / nit), and what's
wrong. Be specific — "the schema is wrong" is useless;
"rounds.id should be UNIQUE NOT NULL because X" is useful.

**{{OPTIONAL_THIRD_SECTION_HEADING}}**: {{describe — e.g. "Open
questions you're uncertain about" for design docs, "Step B
implications" for implementation PRs that constrain future work,
"Should-fix non-blocking" for ready-to-merge PRs with minor items.}}

{{TEST_INSTRUCTIONS — e.g. "Run `pytest` and confirm 473 passed +
3 skipped" for code PRs, "Do not run tests — there's no code in
this PR" for design docs.}}

---

## Filled-in examples

### Design-doc review (e.g. dual-write design rev N)
- VERDICT_LABELS: `{ready-to-implement, more-changes, blocked}`
- BLOCKER_OR_CHANGES_HEADING: `Decisions still wrong`
- OPTIONAL_THIRD_SECTION_HEADING: `Open-question answers`
- TEST_INSTRUCTIONS: `Do not run tests — there's no code in this PR.`

### Implementation PR review
- VERDICT_LABELS: `{merge, changes-requested, blocked}`
- BLOCKER_OR_CHANGES_HEADING: `Specific issues`
- OPTIONAL_THIRD_SECTION_HEADING: `Step B implications` (or
  `Should-fix non-blocking`)
- TEST_INSTRUCTIONS: `Run pytest and confirm N passed + M skipped.`

### Re-review of a revised PR
Add at the top of the context paragraph:
> Your previous verdict was {{PREV_VERDICT}}. Verify each of your
> previous decisions was addressed — partial addresses are not
> closes. Do not re-review items already approved in earlier
> rounds.

This narrows scope to "did the revisions land correctly" and
prevents re-litigating settled decisions.
