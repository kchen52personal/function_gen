# Website + functionality pipeline

## Public isolated runner: development task parallelism

For future runs through `run_public_end_to_end.py` or
`launch_public_detached.py`, up to **seven development tasks** now run at once
by default. Set `--development-workers 1` for sequential execution, or 2–7 to
choose a smaller or larger bounded pool; detached configurations accept the
integer `development_workers`. This is separate from the older
`--parallel-generation-development` option, which overlaps task generation
with the development batch but did not run development tasks concurrently.

Each task retains its own browser workspace, timeout, broker, network relay,
screenshots, logs and spend attribution. Concurrent results are aggregated in
original task order, not completion order; one worker error fails the batch
closed while preserving other artifacts. The shared central-ledger append
used by the v73 model backend now uses a cooperative process lock. Writers
outside that backend that ignore the lock are not covered by this guarantee.

Seven concurrent browsers can use more CPU, memory, website capacity and API
quota at once, and may trigger rate limits; parallel execution reduces elapsed
time only if those shared resources are available. Different contexts may
still share a live account's server-side state, so simultaneous creation or
editing can interfere even when browser workspaces are separate. Select one
worker when tasks cannot safely operate concurrently. The task-generation/
held-out split, per-agent budgets and verification policy are unchanged.
Existing running processes keep their already loaded code. These changes have
only been verified offline; no live seven-worker speedup is claimed.

This setting is for the public isolated runner; it does not change the separate
`run.py` CLI described below.

The only required inputs are a public HTTPS website URL and a read-only
functionality idea. The default CLI now uses the experimental strict
development-verification mode described in [VERIFIER.md](VERIFIER.md):

```bash
python research/website_tool_suite_generator_v1/site_neutral_task_pipeline_v1/run.py \
  'https://example.org/' 'Describe the desired public functionality here.'
```

The default creates one function only after eight WebWright submissions also
pass independent assessment. Add `--max-replacement-rounds 5` to permit up to
five replacement rounds; the default is two. If eight accepted cases are not
reached within the bounded rounds/time budget, the run stops without opening
held-out tasks. Automated acceptance is evidence, not ground-truth correctness.

With `--final-only --webwright-only`, development inclusion depends on
WebWright submission and script integrity, without an independent assessment.
After the function is frozen, held cases are run but **not graded**: an exit
code is not a correctness score. This mode still uses code-safety screening
and existing development-time format feedback; neither is a semantic verifier.

The script prints its unique artifact directory. This is the same implementation
for every site; it contains no per-domain result selectors or authored tools.
The runner and its browser/model helpers live under `runtime/`, without a
default website or an import from a site-specific experiment.

The older non-strict adaptive mode remains available through the Python API;
the historical description below describes that mode, **not the current CLI
default**. See [VERIFIER.md](VERIFIER.md) for the strict default and limitations.

1. Reconnoiter up to three pages and have Qwen Flash propose one typed function
   contract, a task template, and twelve parameter sets.
2. Check exact input uniqueness; reserve eight development and four test tasks.
   The task-generating model sees all proposals; the code-writing model does
   not receive held task inputs before the function is frozen.
   After seeded shuffling, the splitter swaps a held case with a development
   case if a held string appears in the shared contract. This protects the
   existing exact-string boundary but makes the held selection conditional;
   numeric values and semantic similarities are not covered.
3. Run each development task with Qwen Plus and WebWright, allowing bounded
   self-reflection. When a development script changes, automatically run a
   format smoke test on the current disclosed development case and give the
   agent the resulting errors. This is feedback, not a correctness guarantee.
   Fresh-run submitted code and assess it with generic
   first-party grounding and a separately generated blind `qwen3.8-flash` API
   reference. The writer uses Qwen as well, so correlated model-family errors
   remain possible. No person or interactive ChatGPT is used at runtime.
   If an attempt times out after saving a hash-checked format-smoke checkpoint,
   that draft may be independently assessed. A smoke pass or an unfinished
   agent attempt is not itself a verified or successful task.
4. Record all submissions, timeouts, failures, and inconclusive checks. If
   fewer than eight tasks were submitted by WebWright, request replacement
   development tasks for the shortfall. At most two replacement rounds are
   attempted. A private fingerprint check removes exact duplicates with any
   previous or reserved input without providing held input values to the model.
5. Once two WebWright trajectories have been submitted, synthesize a checkpoint;
   repeat after each additional submission, through eight. Provide their actual
   assessment statuses to synthesis, including failures and inconclusive results;
   submissions are not assumed correct. Each checkpoint is run on its
   development cases, and is eligible to freeze if at least two fresh executions
   meet the format/artifact contract. This is a basic readiness check, not proof
   of task correctness; semantic assessments are recorded separately.
6. Only after all generation and checkpoint selection has ended, evaluate
   each frozen checkpoint on the same four held tasks. Never repair a function
   or generate more development tasks using held-out feedback. These repeated
   held-task scores are exploratory, not a clean final model-selection test.

Each API request is written to the project spend ledger. Each development
attempt has up to 30 minutes, with 40 seconds per task added to the batch
allowance; the batch is also capped by the remaining six-hour overall budget.
When three separate command logs contain a connection/refusal or DNS failure
for the target host, the development runner stops the attempt and batch and
the adaptive loop stops rather than generating replacements for an apparent
outage. This is a conservative operational signal, not proof the website is
globally down or that a model is incorrect. It can stop on transient errors.
The overall limit is checked between phases; an
already-running API call or assessment can finish after that limit. The system
can stop incomplete at any gate, including a
blocked page, invalid task generation, disagreement, unavailable model API,
or insufficient development evidence.

## Important limitations

- This is an experimental site-neutral design, **not a guarantee that it works
  on every website**. Login-only sites, CAPTCHAs, location restrictions,
  inaccessible content, unsupported parameter types and dynamic pages can
  prevent it from succeeding. The design covers only public read-only goals.
- Model-generated tasks are not ground truth. Exact parameter disjointness is
  checked, but semantic near-duplicates, pretraining contamination, and
  provider/model correlations are not ruled out.
- Repeatedly comparing or choosing checkpoints using the four held tasks spends
  that set for model selection. A separate, untouched confirmation set is
  needed for a stronger final number. Agreement is not human verification.
- Both candidate and blind-reference output are grounded against first-party
  pages; agreement is useful evidence, not proof of semantic correctness. Live
  data can change between runs, and strict matching can reject correct answers.
- The generated-code sandbox restricts filesystem and process access, but does
  **not** enforce a website network allowlist or browser-level read-only
  behavior. Prompted safety restrictions are not a hard security boundary.
- The output reports `complete_automated_assessment_only`; it must not be
  called human-verified or universally correct. Tests with mocked sites verify
  control flow, not real-world cross-site performance.
