# thunderkit — North Star

> **Big work in big repos is won by decomposition + heterogeneity, not by one smart model.**

thunderkit is an *opinionated* skill pack. It exists to take a large change in a large
repository and:

1. **Decompose** it into disjoint, dependency-layered lanes that don't collide.
2. **Route** each lane to the best model *and* harness for that kind of work.
3. **Run** those lanes in parallel across a heterogeneous fleet (Bedrock Fable 5.1,
   Claude Opus, Codex Sol — via the `claude`, `codex`, and `opencode`/`hermes` harnesses).
4. **Review** the result across at least two model families before calling it done.
5. **Remember** the project's intent as a committed artifact, not a chat transcript.

## The opinions (non-negotiable — skills enforce these as gates)

1. **No single-model plans.** A plan that can't be split into parallelizable lanes isn't
   finished. `tk-plan` emits lanes or it fails.
2. **Review is always cross-family.** A model family reviewing its own output is not review.
   `tk-review` fans work to ≥2 families (e.g. Opus + Sol) and consolidates.
3. **Done is evidence, not intent.** Every lane carries a verification command. No command,
   no "done" — the lane is *blocked*.
4. **The fleet is heterogeneous and partially unavailable.** Skills degrade to the agents
   actually installed and authenticated, and *name* what's missing instead of failing dark.
5. **The user picks load-bearing models.** When the model choice materially changes the
   outcome (the critical path, the final review), thunderkit offers the top models and asks —
   it does not silently default.
6. **Context is committed.** A project's opinion and decisions live in `.thunderkit/`
   (`NORTH_STAR.md` + a decision log), so they travel with the repo across sessions and agents.

## What thunderkit is not

- Not a UX / SEO / design / payments / delivery framework. It has exactly one concern:
  turning big-repo changes into parallel, cross-reviewed, evidence-gated work.
- Not a plugin marketplace, hook system, or native runtime installer. It distributes
  `SKILL.md` files and small local helpers; its npm pointer installs Thunderkit skills only.
- Not coupled to a private orchestrator or a universal host adapter. Native workflow reuse is
  qualified per host, operation, source version, model binding, and safety boundary. Portable
  procedures still require suitable tools and reachable selected models.

## Architecture and boundaries

Thunderkit retains model-class selection, lifecycle policy, portable project context and
evidence-based completion. It reuses native implementation only where the
[dependency registry](skills/references/dependencies.json) declares a compatible target:
OMO on OpenCode or Codex, OMH on Hermes. Other skill-compatible hosts use Thunderkit-owned
procedures; installing readable skill files does not promise native execution support.

The peers are optional and separately installed, activated and checked by the operator.
Thunderkit neither bundles their workflow bodies nor installs or activates them. Its
[dependency guide](DEPENDENCIES.md) records exact versions, licenses and manual instructions.
One native handoff owns the scoped workflow; it does not run beside a duplicate owned loop.

The user chooses `classes.planner`, `classes.executors` and `classes.reviewers`. Backend choice
cannot replace those selections or weaken the independent reviewer-family gate. Missing peers
produce named owned fallbacks; missing explicit models, unsupported mappings or unmet safety
requirements block work when no compliant procedure exists. This is how partial availability
degrades honestly without changing the project's opinion.

## Why heterogeneity

Different work has different cost/quality curves. Wide exploration and cheap retrieval want a
fast, login-free model (Fable 5.1). The critical implementation path wants the strongest coder
(Opus). Review wants a *different* family than the author, so blind spots don't survive. Sol
(Codex) brings a genuinely different lineage for that second opinion. One model can't be all of
these at once; a fleet can — if the work is decomposed to feed it. That decomposition is the
whole game, and it's what thunderkit is opinionated about.
