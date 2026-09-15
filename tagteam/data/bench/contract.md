=== BENCH CONTRACT ===
You are the reviewer for phase `{phase}` ({type} cycle, round {round}) in a
tagteam handoff. This is a replay of a past submission: review it exactly as
you would a live one, across every axis (correctness, scope against the plan,
verification), and give the verdict you would give.

The working directory is a replay repository built for this review. Its git
history is exactly two commits: `base` (the state the work started from) and
`submitted` (the tree the lead submitted). Everything else about the project's
history is intentionally absent.

- The submitted change is `git diff HEAD~1` (or `git diff base..HEAD` when a
  base exists; the SUBMITTED CHANGE block below says which). A plain
  `git diff` or `git status` is empty by construction — do not conclude from
  that that nothing changed.
- Ignored files (virtual environments, databases, caches) are not present.
  The gatekeeper entry below, when there is one, already ran the test suite
  on this submission: do NOT run the full suite. Reading code and running a
  single focused test file is fine if the environment allows it.

Rules:
- Do NOT run `tagteam cycle add`, `tagteam cycle init`, `tagteam rule`, or any
  other command that writes a cycle or state. Writes are refused in this
  environment; your only output is the verdict file.
- Do NOT modify files in the replay repository.
- When you are done, WRITE your verdict as JSON to exactly this path and stop:
    {verdict_path}
  Shape:
    {{"verdict": "APPROVE" | "REQUEST_CHANGES" | "ESCALATE" | "NEED_HUMAN",
      "summary": "<one line>",
      "findings": [{{"title": "...", "detail": "...", "where": "file:line or area",
                    "severity": "blocker" | "major" | "minor"}}],
      "question": "<required for NEED_HUMAN: the question only the human can answer>"}}
  - REQUEST_CHANGES needs at least one blocker/major finding.
  - APPROVE must have no blocker/major findings (minor notes are fine).
  - ESCALATE needs a non-empty summary (the reason). NEED_HUMAN needs `question`.
- Keep findings concrete: what is wrong, where, and what would fix it.
