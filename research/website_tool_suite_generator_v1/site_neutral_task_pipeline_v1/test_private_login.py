from runtime.public_login_bootstrap import session_cookie_present


def test_session_marker_requires_value_name_and_matching_domain():
    cookie = {'name':'session', 'value':'private-test-only', 'domain':'.example.org'}
    assert session_cookie_present([cookie], 'session', 'www.example.org')
    assert not session_cookie_present([cookie], 'other', 'www.example.org')
    assert not session_cookie_present([cookie], 'session', 'badexample.org')
    assert not session_cookie_present([cookie], 'session', 'example.org.attacker.test')
    assert not session_cookie_present([{**cookie, 'value':''}], 'session', 'example.org')
    assert not session_cookie_present([{**cookie, 'domain':''}], 'session', 'example.org')
