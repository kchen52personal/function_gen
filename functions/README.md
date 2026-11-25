# Generated functions for review

These functions were produced by current or earlier automated pipelines. They
are preserved examples, not deployment-ready tools. The human chose the website
or goal; “automated” refers to code generation during the recorded run. The
manually built site pilots are intentionally excluded.

| Function | Parameters and output | Recorded result and limitation |
|---|---|---|
| [arXiv: search papers](arxiv/search_papers.py) | `query` and `limit`; displayed titles and abstract links. | Eight submitted development examples were supplied to synthesis. Four held processes exited 0 but were not independently graded for semantic correctness. |
| [Youdao: create a note](youdao/create_note.py) | Natural-language `request`; attempts to select a document type and open an editor. | Three original held executions exited 0 ungraded; the user later confirmed two replayed document types. Creating notes changes account state and needs an authorized account. |
| [Youdao: open an existing note](youdao/open_existing_note.py) | Natural-language `request`; attempts to find and open the requested note. | **Known failure:** all three exact held requests returned that the note name could not be extracted, despite exit 0. Included for investigation. |
| [GOV.UK: popular services](govuk/get_popular_services.py) | `limit`; displayed service titles and links. | An older single-tool pipeline passed an experimental reference check on sampled limits; it was not generalized from a development-task suite. |

The Youdao functions require an authenticated browser setup not included here.
Do not run a mutation without permission. Outputs and live websites may change;
an exit code is not proof that the requested action succeeded.

## Task examples and prompts

For arXiv, the [development requests](tasks/arxiv/development.json),
[held requests](tasks/arxiv/held_out.json),
[task-generation prompt](prompts/arxiv/task_generation_01.json), and
[synthesis prompt](prompts/arxiv/synthesis_01.json) are preserved. The held
requests have already been inspected and are no longer a blind final test.

The original Youdao task files and raw prompts contain test-account names and
potentially account-specific page information. They are intentionally omitted
from this public package; the earlier authorization to use them for the test
does not establish permission to publish them. The function code remains for
review. The GOV.UK example used a different generation pipeline and did not
have the same task-suite generalization process.

## Common synthesis instruction excerpt

The recorded Qwen synthesis requests asked the model to combine submitted
development scripts into one generic Playwright program. They also cautioned
that submitted scripts and trajectory excerpts are not proof of correctness,
and asked it to support valid parameter values beyond the examples. The full
arXiv synthesis request is linked above.
