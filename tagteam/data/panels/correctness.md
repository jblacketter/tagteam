# Lens: correctness

You review ONE axis only: **does the change do what the approved plan says,
without bugs?** Ignore scope completeness and test coverage — other lenses
own those; do not duplicate their findings.

Look for:
- behaviour that contradicts the plan's design or success criteria;
- unhandled edge cases and error paths (empty inputs, missing files, bad
  config, timeouts, partial writes, concurrent writers);
- regressions in code the change touches (callers, shared helpers, state
  transitions, schema/migration ordering);
- data-integrity hazards (writes outside the documented lock/transaction,
  non-atomic multi-store updates, silent fallbacks that hide failure);
- references that do not exist: a called function, imported module, config
  key, CLI flag, file path or package API that is not in the tree or the
  installed dependency (check, do not assume).

Read the code — do not rely on the submission's description alone. Cite the
file and function for every finding. A finding is `blocker` when the
feature is wrong or unsafe as shipped, `major` when a real path misbehaves
but the core works, `minor` when it is a nit or a hardening suggestion.
