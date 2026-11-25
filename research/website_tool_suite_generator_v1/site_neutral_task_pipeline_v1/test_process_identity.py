from process_identity import current_identity, inspect_identity


def test_current_process_identity_matches():
    result=inspect_identity(current_identity())
    assert result['status'] in {'visible','unknown'}


def test_unknown_and_pid_reuse_are_not_reported_active():
    assert inspect_identity(None)['status']=='unknown'
    identity=current_identity()
    if identity['linux_start_ticks'] is not None:
        identity['linux_start_ticks']+=1
        assert inspect_identity(identity)['status']=='identity_mismatch'
