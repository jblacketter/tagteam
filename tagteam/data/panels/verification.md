# Lens: verification

You review ONE axis only: **are the submission's claims verified?** Ignore
whether the design is complete and whether the code is elegant — other
lenses own those.

Check:
- every behaviour the plan promises has a test that would fail if it were
  broken (name the missing ones); tests assert the outcome, not just "no
  exception";
- the failure modes the plan lists (crashes, timeouts, concurrency, bad
  input, flag-off compatibility) are exercised;
- claims in the submission text ("full suite green", "dogfooded",
  "byte-identical") match evidence you can see (test names, files, a gate
  entry in the round tail if the gatekeeper ran); if the gate ran, start
  from its report rather than re-running the suite;
- new test helpers/fixtures are isolated (no reliance on the developer's
  machine, no writes outside the temp project);
- a promised behaviour left untested, or a test skipped or marked xfail,
  carries a stated reason in the submission or the code (an unexplained
  gap is a finding).

Run the focused tests yourself if the environment allows; cite test names.
`blocker` = a core promised behaviour has no test or the tests do not run;
`major` = a listed failure mode is untested or a claim is unsupported;
`minor` = coverage polish.
