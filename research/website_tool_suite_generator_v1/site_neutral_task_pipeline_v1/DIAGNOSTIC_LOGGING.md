# Diagnostic logs

Every new public run keeps the configuration, launcher log, launch identity,
source snapshot and source hashes. Old runs are preserved, not retroactively
represented as having logs they never wrote.

| Artifact | What to inspect |
|---|---|
| `events.jsonl` | Append-only UTC/monotonic timeline; process/thread, stage, start/finish, operation IDs, counts and artifact pointers. Concurrent writes are locked and flushed. |
| `summary.json` | Latest controller state, budgets, selections, hashes, held-open flag and outcomes. Not a live count of a batch still running. |
| `tasks/generation_progress.json` | Every completed generation batch, validation rejection, duplicate rejection and accepted count. |
| `tasks/generation_pool.json` | Recoverable inputs and identity metadata, source hash when resumed, explicit split/usage state. No expected answers. |
| `tasks/requests_so_far.txt` | Exact backend-authored requests as they are accepted. |
| `tasks/development_freeze.json` | Early development hash and timestamp; these rows cannot later become held tests. |
| `tasks/request_catalog.json` | Final exact development/held requests, never supplied to the function writer. |
| `development_rounds/round_XX/summary.json` | Current task/workspace, completed attempts and per-attempt times/budgets. |
| `.../TASK/qwen3.5-plus/task_request.json` | Exact task sent to WebWright, including failed attempts. |
| `.../TASK/qwen3.5-plus/{config.yaml,stdout.log,stderr.log}` | Resolved model instructions and limits, process output. |
| `.../TASK/qwen3.5-plus/agent/` | Workspace snapshot: trajectory, raw responses, saved step commands/output, scripts and screenshots when produced. Original workspace is retained too. |
| `.../TASK/qwen3.5-plus/command_events.jsonl` | Host-side step, command and automatic smoke-check start/finish events, UTC/monotonic timestamps, durations, return codes, command length/hash and smoke classification. No raw command or page output is copied into this timeline. |
| `.../TASK/qwen3.5-plus/model_events.jsonl` | Main-agent and inner-tool model-request start/finish events, call IDs, model names, durations and provider-reported usage where available. A start without a finish indicates interrupted/unknown, not zero usage. |
| `.../TASK/qwen3.5-plus/timing_summary.json` | Automatically generated wall time, step/command/smoke/model timing aggregates, union of overlapping model intervals, unclosed-operation counts, model usage completeness, and network reason counts. It explicitly marks unavailable input files. |
| `.../TASK/qwen3.5-plus/failure_diagnostics.json` | Last saved step, error signals, missing evidence/archive errors, cancellation flag, completed-call timing summary. Signals are hypotheses, not proven causes. |
| `network.jsonl` | UTC, tunnel destination, start/allow/deny/close status, correlation ID, phase, fixed reason code, connection-budget count/limit and elapsed time; normal closes include aggregate byte counts. HTTPS is not decrypted; this is not a full HTTP trace. |
| `checkpoints/checkpoint_XX/` | Synthesis request/response logs, candidate, frozen hash and source task IDs. |
| `.../held_checks/TASK/` | Exact test request, frozen script execution, partial stdout/stderr and workspace even after timeout; summary/diagnostics. No WebWright re-solving. |
| `shadow_verifier/` | Post-run diagnostic reviews only; no acceptance or function-generation feedback. |
| `/shared/hans/webarena/spend_ledger.jsonl` | API spend records; unavailable token/cost values remain unknown, not zero. |

Start with the root timeline, follow its task/artifact pointer, then compare
the last saved command, network failures and API timings. A start without a
finish means interrupted/unknown. Do not count it as completed or free.

## Parallel operation

The isolated public runner defaults to at most seven concurrent development
tasks (`development_workers`, configurable from 1 to 7). Each worker has a
separate workspace, agent, relay, broker, request timelines and task artifacts;
results are aggregated in the original task order. Browser contexts are not
isolated from server-side changes in a shared authenticated account. Workers
can consume more CPU, memory and API quota concurrently or encounter rate limits.
The experimental speedup has only been checked offline, not on a live batch.

The separate opt-in `parallel_generation_development` overlaps the development
batch with held-request generation. The first N
validated requests are frozen as development before execution. Additional
requests are held inputs; the generator gets prior inputs/identities but no
browser outcomes. No later reshuffle is permitted. Only an unsplit, matching,
identity-validated checkpoint can be resumed by this path.

Worker results are consumed once by the controller. Cancellation joins the
worker and saves its partial attempt before post-run review. Synthesis and
replacement selection still wait for the final split registry. Tests open
only after function generation is closed and the candidate hash is frozen.

## Limits

- Preserve failures, not only submitted trajectories. Missing or incomplete
  evidence is explicitly reported; a killed in-flight step may never save.
- stdout/stderr capture is bounded at 4 MiB each for WebWright. Snapshots cap
  at 250 MB/2,500 files; a failed snapshot does not delete the original workspace.
- API timing reports completed logged calls. Overlapping durations are not
  additive; provider queueing versus network/inference cannot be inferred.
- The new metadata-only command and model timelines are host-owned and useful
  for in-flight operations, but generated content, screenshots and existing raw
  debug/response artifacts can still contain private account data. Treat all
  run directories as private; review them before sharing. Hashes and hostnames
  can themselves be sensitive in some contexts.
- Command timings do not automatically expose the duration of each Playwright
  click inside a generated script. The relay sees encrypted HTTPS tunnels, not
  page bodies or all HTTP response statuses. Logging may fail on filesystem
  errors, in which case a warning is written to the runner's stderr.
- Credentials/session files are not copied into agent snapshots or this
  timeline. No environment or cookie dump is requested. Generated browser code
  can still access active cookies; this is not a perfect secret-isolation or
  automatic-redaction guarantee. Keep raw browser artifacts private.
- Saved screenshots alone cannot establish advancing playback; clicking play,
  formal submission, successful execution and semantic correctness differ.
- Abrupt host failure/SIGKILL can leave the last event incomplete. Logs cannot
  recover evidence that was never written.
