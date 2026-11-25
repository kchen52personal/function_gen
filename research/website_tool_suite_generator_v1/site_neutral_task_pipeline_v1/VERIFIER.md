# Development verifier: experimental, site-neutral mode

## What it does

For each hash-matched WebWright submission, the pipeline:

1. Executes the submitted Playwright script again in a fresh workspace on its
   development task; checks the typed inputs, output contract, first-party
   source URL and required screenshots.
2. Opens the reported first-party source in a fresh browser and checks whether
   result values appear in the page's observable content. This is existence
   evidence, not proof that the result answers the question.
3. Uses a separate Qwen 3.8 Flash background API agent to independently try the
   same task with the task request and contract, without the candidate code or
   answer in its prompt. It executes and grounds that result too, then compares
   the two structured outputs.
4. Accepts the submission for synthesis **only** when the format checks,
   grounding and independent result agree, and the submitted script hash still
   matches. A known contract failure is rejected. A timeout, unavailable
   reference, missing evidence, or disagreement is inconclusive, not a pass.
5. Generates replacement development tasks when fewer than eight are accepted,
   subject to existing attempt, cost, access and time limits. It then builds a
   function from accepted submissions only and repeats the checks on its
   development cases. If any are not accepted, it stops without opening held
   tasks. Otherwise, it freezes the candidate before running the four held tasks.

All model calls use backend APIs, not an interactive assistant or a human in
the runtime loop. No per-site task, selector, expected answer, or manual result
is added to the verifier. Existing spend logging applies to the reference
agent; each assessment is saved under the run's `development_checks`.

## Limitations and fairness

- **Experimental:** passing is model agreement plus supporting evidence, not
  a correctness guarantee. Both agents can misunderstand the same task or
  return the same wrong result. Verifier accuracy has not been calibrated.
- Reopening a page and requiring exact output agreement can be too strict for
  changing rankings, valid equivalent representations, calculated values,
  multi-page results, empty results and non-text evidence. A correct result
  may be inconclusive or rejected for an output-contract mismatch. Passing
  values found on a page does not prove associations, ordering, completeness,
  relevance, or semantic correctness.
- The same model family is used for writer and reference; their failures can
  correlate. Web content is untrusted. Browser network isolation and strict
  read-only behavior are not fully enforced.
- The test-task generator initially proposes all twelve cases before the
  split. Exact duplicates are checked, but near-duplicates, semantic overlap,
  and model pretraining contamination are not ruled out. The code writer and
  development verifier receive only development cases before freeze. Do not
  tune or choose models using held outcomes; once used for selection, reserve
  a fresh test set.
- Requiring eight accepted trajectories and independently assessing the final
  function on all eight is strict and may frequently stop. The extra model
  calls and fresh browser runs can take considerable time and money; the
  six-hour budget is checked between stages, not a hard interruption of every
  already-running call. Failed and uncertain cases remain in the record.

## Run

The normal CLI uses this mode for new runs, with a single synthesis after eight
accepted development trajectories:

```bash
python research/website_tool_suite_generator_v1/site_neutral_task_pipeline_v1/run.py \
  'https://example.org/' 'Describe the public, read-only functionality.' \
  --max-replacement-rounds 5
```

`--final-only --webwright-only` remains available for the old submission-only
experiment; it does not verify correctness, and its held outputs are ungraded.
Existing running jobs retain the code and selection mode they started with.

## Checks performed during implementation

The local suite passed 73 tests. Mocked tests cover accepted/rejected/inconclusive outcomes, hash changes,
timeouts, exclusion of failed examples, replacement and the held-data freeze.
They are not a live accuracy benchmark. An offline format replay of four
previously submitted development artifacts found four interface failures:
two mismatched output keys and two unreadable final-log markers. The declared
key in those old runs was itself the accidental placeholder `field_name`;
therefore those failures do **not** establish that the website answers were
wrong. They show why task contracts and formatting need separate attention.
Only development artifacts were inspected; no held tasks were opened.
