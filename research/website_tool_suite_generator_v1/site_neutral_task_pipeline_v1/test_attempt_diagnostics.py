import json
from attempt_diagnostics import collect


def test_records_location_and_failure_signals_without_grading(tmp_path):
    path=tmp_path/'debug/steps';path.mkdir(parents=True)
    (path/'step_0001.json').write_text(json.dumps({'step':1,'command_text':'click observed button',
        'outputs':[{'output':'strict mode violation: locator resolved to 2 elements'}]}))
    (path/'step_0002.json').write_text(json.dumps({'step':2,'command_text':'inspect player',
        'outputs':[{'output':'Page.screenshot: Target crashed'}]}))
    (path/'step_0003.json').write_text('{partial')
    data=collect(tmp_path,124,{},'process timed out')
    assert data['last_saved_step']['step']==2
    assert data['signal_counts']=={'locator_ambiguity':1,'browser_crash':1}
    assert data['stop_signals']==['wall_timeout']
    assert data['unreadable_step_files']==['debug/steps/step_0003.json']
    assert data['semantic_verdict'] is None


def test_missing_trajectory_still_has_runner_diagnostic(tmp_path):
    data=collect(tmp_path,130,{},runner_error='KeyboardInterrupt')
    assert not data['trajectory_present'] and data['last_saved_step'] is None
    assert data['runner_error_type']=='KeyboardInterrupt'
