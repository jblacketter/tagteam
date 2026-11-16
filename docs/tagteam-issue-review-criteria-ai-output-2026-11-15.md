# tagteam issue: review criteria do not cover AI-output hazards or plan-level risks

Drafted 2026-11-15 from the Aegis (`~/projects/QA`) session, tagteam 3.14.11.
Written by the lead session (claude) at Greg's request. Not started: this is
a candidate phase to open after Phase 77 (`panel-turn-notice`) closes.

> **Arbiter's ruling, 2026-11-16: split.** Tagteam takes only the two items that are true of
> AI-written work in any project (nonexistent references; unexplained skips, xfails and untested
> promises), as lens text: Phase 78, `docs/phases/review-criteria-ai-output.md`. The contract
> sentence and the dependency, rollback, security/performance and secrets items are one project's
> review policy and stay in that project (`AGENTS.md` or a lens override). The patch below is
> kept as the record of what was proposed; it is not applied whole.

## Why

The retired `unified` experiment had four review checklists. Compared with
what tagteam feeds reviewers today, these items had no home:

- **Impl review:** references that do not exist (hallucinated functions,
  modules, config keys, CLI flags, paths, package APIs); added dependencies
  justified, current and not duplicated; no hardcoded secrets in tests or
  fixtures; skipped or xfail tests carry a stated reason.
- **Plan review:** new dependencies justified; a migration or rollback path
  where data or deployed behaviour changes; security and performance risks
  named, or the plan says why none apply.

`data/checklists/*.md` is the wrong place: those files are frozen provenance
(Phase 61/62, pinned to v3.14.1 by `test_retired_path_sources_are_frozen_at_the_tag`)
and only `bench` reads them. The live criteria are the panel lens prompts
(`tagteam/data/panels/*.md`, used only when `panel.enabled`) and the reviewer
step in the handoff contract (used on every review).

## Draft change

Applies cleanly to `main` at `1c3cc82` (`git apply --check -p1`, run
2026-11-15). Save the block below as a file and `git apply` it.

```diff
diff --git a/plugin/skills/handoff/SKILL.md b/plugin/skills/handoff/SKILL.md
index 7dc63c7..206feb1 100644
--- a/plugin/skills/handoff/SKILL.md
+++ b/plugin/skills/handoff/SKILL.md
@@ -112,6 +112,7 @@ This appends an amendment to the active round without bumping the round number o
    - If the gatekeeper is enabled, the round tail also carries the gate's entry (`role: gatekeeper`, `GATE: PASS | tests ok (…) | scope N paths | plan-doc ok`): the tests already ran and the scope was already checked before your turn — start from those facts. A `GATE_PASS` whose content begins `GATE: checks failed but bounce cap … reached` means the lead hit the bounce cap; the failures are in the entry and the decision is yours (review anyway, request changes, or escalate).
    - **Do not re-run the full suite.** When a gate entry (`GATE: PASS … / checked: HEAD <sha>`) or the submission reports a full-suite result for the submitted commit, take it as fact — the lead is accountable for it, and the suite is expensive. Verify by reading the diff and, at most, running the test files it touches. Re-run the whole suite only if you have a concrete reason to doubt the report (the checked commit differs from HEAD, the numbers do not add up, a focused run fails) — and say why in your entry. If the gate is on but no `GATE:` entry has appeared yet, the gate is still owed (`tagteam gate status`) — wait for it rather than substituting your own run.
 2. Review the referenced plan/implementation files
+   - **Plan review:** besides scope and success criteria, check that every new dependency is justified, a migration or rollback path exists where data or deployed behaviour changes, and security and performance risks are named (or the plan says why none apply).
 3. Choose ONE action (all commands update both cycle and state in one call):
    - **APPROVE:** `tagteam cycle add --phase [phase] --type [plan|impl] --role reviewer --action APPROVE --round [N] --updated-by [your-agent-name] --content "Approved."`
    - **REQUEST_CHANGES:** For detailed feedback, use stdin with a heredoc. The system auto-escalates to the human arbiter when it detects 10+ consecutive stale rounds (lead re-submitting identical content with no progress).
diff --git a/tagteam/data/panels/correctness.md b/tagteam/data/panels/correctness.md
index 00803a5..2ee4947 100644
--- a/tagteam/data/panels/correctness.md
+++ b/tagteam/data/panels/correctness.md
@@ -11,7 +11,15 @@ Look for:
 - regressions in code the change touches (callers, shared helpers, state
   transitions, schema/migration ordering);
 - data-integrity hazards (writes outside the documented lock/transaction,
-  non-atomic multi-store updates, silent fallbacks that hide failure).
+  non-atomic multi-store updates, silent fallbacks that hide failure);
+- references that do not exist: a called function, imported module, config
+  key, CLI flag, file path or package API that is not in the tree or the
+  installed dependency (check, do not assume);
+- dependency changes: every added package is justified by the plan or the
+  submission, pinned to a current release, and not duplicating one the
+  project already has;
+- secrets: no hardcoded keys, tokens or credentials, including in tests,
+  fixtures and examples.
 
 Read the code — do not rely on the submission's description alone. Cite the
 file and function for every finding. A finding is `blocker` when the
diff --git a/tagteam/data/panels/verification.md b/tagteam/data/panels/verification.md
index be0bc9f..4e5bf61 100644
--- a/tagteam/data/panels/verification.md
+++ b/tagteam/data/panels/verification.md
@@ -15,7 +15,10 @@ Check:
   entry in the round tail if the gatekeeper ran); if the gate ran, start
   from its report rather than re-running the suite;
 - new test helpers/fixtures are isolated (no reliance on the developer's
-  machine, no writes outside the temp project).
+  machine, no writes outside the temp project);
+- a promised behaviour left untested, or a test skipped or marked xfail,
+  carries a stated reason in the submission or the code (an unexplained
+  gap is a finding).
 
 Run the focused tests yourself if the environment allows; cite test names.
 `blocker` = a core promised behaviour has no test or the tests do not run;
```

## Not done in the draft

- The second contract copy, `tagteam/data/.claude/skills/handoff/SKILL.md`,
  needs the same line (it is byte-identical to the plugin copy on `main`).
- Contract bookkeeping: the old contract's hash likely goes into
  `tagteam/data/vendored_contract_hashes.json`, plus a release bump. Which
  tests enforce this was not checked.
- No tagteam tests were run against the patch.

## Related observation

`tagteam upgrade --preview` run from Aegis previewed all 46 registered
projects, and would create framework files in some (for example
`~/projects/temp`). It also only manages a project's root, so nested
`packages/*/docs/checklists/` and `packages/*/templates/` copies left by a
monorepo fold-in are invisible to it (Aegis removed its copies by hand in
aegis PR #104). A per-project scope flag, or a note in `upgrade`'s usage,
would have avoided the surprise.
