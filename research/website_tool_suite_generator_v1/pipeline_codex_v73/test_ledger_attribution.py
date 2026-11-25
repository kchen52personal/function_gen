from pathlib import Path

from webwright_yibu_model import ledger_context


def test_concurrent_stage_records_retain_distinguishable_log_paths() -> None:
    root = Path('/shared/hans/run/attempt_01/version_00')
    preflight = ledger_context(root / 'visible_reference_advisory/api_call.json')
    reference = ledger_context(root / 'independent_reference_fallback/api_call.json')
    generator = ledger_context(root / 'api_call.json')
    assert preflight['experiment_stage'] == 'visible_reference_advisory'
    assert reference['experiment_stage'] == 'independent_reference'
    assert generator['experiment_stage'] == 'tool_generation'
    assert len({preflight['experiment_artifact_path'], reference['experiment_artifact_path'], generator['experiment_artifact_path']}) == 3
