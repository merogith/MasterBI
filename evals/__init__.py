"""Scored regression suites for the AI layer.

**What this is, stated before anything else, because the honest scope is
narrower than the phrase "eval harness" usually implies.**

There is no `ANTHROPIC_API_KEY` in this environment and none in CI — `client.py`
looks for `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` and neither exists, so
the AI layer is off and `tests/ai.py` runs entirely against a transcript
player. That is a deliberate property of the suite, not an accident: "a test
that needs a key is a test that does not run, and a gate nobody runs is not a
gate."

The consequence for this package is precise. **These evals score the gates,
not the model.** Every case's model-side output is authored or derived rather
than recorded, so a passing run says "`verify.py` catches an invented figure"
and never says "the narrator does not invent figures". The day a key exists,
recorded transcripts drop into `cases/` in the same format and the same
scorers grade them — which is the whole reason the format separates the
*output* from the *expectation*.

Claiming otherwise would be the failure this repo keeps finding: 4.4 refused to
write Damodaran's name against a number nobody fetched, and this refuses to
call an authored corpus a measurement of the model.

## Why this is worth having anyway

The gates are what protect the product. `verify.py` is what stands between a
hallucinated figure and a board pack; `planner.validate` is what stands between
a model and the profile. Both are ordinary Python, both are decidable, and
both can be scored over a corpus that grows every time something goes wrong.

`tests/ai.py` already asserts *behaviours* — does the gate refuse this one
invented number, does the retry carry the violation back. What it cannot do is
report a **rate**. An eval says "62 of 62 adversarial figures were caught", and
a rate is a thing a threshold can hold and a regression can visibly move.

## What is deliberately absent

* **A mapper scorer.** 6.3's brief lists planner, narrator and mapper. Measured
  before building: `kpi_maker/ingest/mapping.py` imports nothing from `ai/`,
  and the only consumers of the AI layer are the planner and the narrator. The
  mapping assistant is a **6.1** agent that does not exist yet, and an eval for
  an agent nobody wrote is the dead-spec-field pattern 0.3 was spent removing.
  It goes in with the agent.
* **LLM-as-judge.** Impossible without a key, and the brief rules it out as a
  correctness gate regardless. Every scorer here is deterministic.

    python -m evals            # run every suite
    python -m evals narrator   # one suite
"""
