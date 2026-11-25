import hashlib
import json

import pytest
from diagnose_run import inspect_diversity, inspect_timing


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_diversity_does_not_disclose_held_inputs(tmp_path):
    dev = tmp_path/'tasks/development/tasks.json'
    held = tmp_path/'tasks/held_out/tasks.json'
    hashes = {'development':write(dev,{'tasks':[{'parameters':{'query':'Blue whale'}}]}),
              'held_out':write(held,{'tasks':[{'parameters':{'query':'Blue whale!'}}]})}
    write(tmp_path/'summary.json',{'split_hashes':hashes})
    result=inspect_diversity(tmp_path)
    assert result['exact_cross_split_parameter_matches']==0
    assert result['lexically_high_similarity_pairs']==1
    assert 'Blue whale' not in json.dumps(result)
    held.write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):
        inspect_diversity(tmp_path)


def test_timing_does_not_treat_submission_as_correctness(tmp_path):
    write(tmp_path/'summary.json',{'status':'development_agents_running'})
    write(tmp_path/'development_rounds/round_00/summary.json',{'status':'running','attempts':[
        {'task_id':'case','trajectory_status':'Submitted','process_exit':0}]})
    result=inspect_timing(tmp_path)
    assert result['batches'][0]['submitted']==1
    assert 'accuracy' not in json.dumps(result)
