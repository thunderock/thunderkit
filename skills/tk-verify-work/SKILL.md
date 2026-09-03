---
name: tk-verify-work
description: "Use to validate built features through conversational walk-through: turns each acceptance criterion into a real user-surface test, tracks pass/fail/gap in UAT.md that survives a context reset, and feeds gaps back to tk-plan."
metadata:
  thunderkit:
    role: uat
    tier: verify
---

# tk-verify-work — conversational UAT

The parallel-thunderkit analogue of GSD's verify-work. `tk-review` proves the code passes its
*verify commands*; `tk-verify-work` proves the built thing actually does what the user asked, by
walking the acceptance criteria through the **real user surface** — not the tests, the surface.

Model class: **reviewers**. Answers use `tk-ask` discipline.

## Procedure

1. Read `SPEC.md`/`PLAN.md` acceptance criteria. Turn each into a concrete walk-through step:
   the action, the expected observable, the surface it happens on.
2. Exercise each on the real surface (run the CLI, hit the endpoint, open the page) — a passing
   unit test is not a substitute for the surface behaving.
3. Record each as pass / fail / gap with the observed result. A `gap` is a criterion the build
   doesn't meet.
4. Persist to `UAT.md` continuously so the session survives a context reset — resume by re-reading
   it, not by re-testing from scratch.

## Output — `.thunderkit/UAT.md`

Per-criterion status + observed evidence. Gaps feed back to `tk-plan` as new lanes (a gap is a
mini-plan, not a "done with caveats"). The phase isn't shippable while any acceptance criterion
is a `gap`.

## Boundary

`tk-verify-work` tests behavior, it doesn't fix it — a gap routes to `tk-plan`/`tk-debug`, not to
an inline patch that skips the loop.
