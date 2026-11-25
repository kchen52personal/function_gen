from run_amazon_verified_pilot import eligible_for_acceptance


def test_acceptance_requires_both_runs_and_independent_oracle():
    runs = {'default': {'exit_code': 0}, 'held': {'exit_code': 0}}
    assert eligible_for_acceptance(runs, [], 0, {'status': 'pass'})
    assert not eligible_for_acceptance({'default': {'exit_code': 0}}, [], 0, {'status': 'pass'})
    assert not eligible_for_acceptance({'default': {'exit_code': 0}, 'held': {'exit_code': 1}}, [], 0, {'status': 'pass'})
    assert not eligible_for_acceptance(runs, ['held input ignored'], 0, {'status': 'pass'})
    assert not eligible_for_acceptance(runs, [], 1, {'status': 'pass'})
    assert not eligible_for_acceptance(runs, [], 0, {'status': 'fail'})
    assert not eligible_for_acceptance(runs, [], 0, {})
