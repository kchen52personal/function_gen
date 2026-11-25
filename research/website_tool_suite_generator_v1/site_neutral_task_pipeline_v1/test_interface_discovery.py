from interface_discovery import discover, safe_url


def test_finds_first_party_cli_and_skill_through_help_link():
    pages = {
        'https://example.com/': '<a href="/help">Help</a><a href="https://evil.com/cli">CLI</a>',
        'https://example.com/help': '<title>Help center</title><a href="/cli">CLI</a><a href="/skill">Skills</a>',
        'https://example.com/cli': '<title>Example CLI guide</title><h1>CLI</h1>',
        'https://example.com/skill': '<title>Agent Skills guide</title>',
    }
    result = discover('https://example.com/', fetcher=lambda url, host, timeout: (200, pages[url]))
    assert {x['kind'] for x in result['candidates']} == {'cli', 'agent_skill'}
    assert all(x['status'] == 'same_site_unverified_candidate' for x in result['candidates'])
    assert all('evil.com' not in x['url'] for x in result['pages_visited'])


def test_menu_mention_is_not_a_documentation_candidate():
    result = discover('https://example.com/', max_pages=1,
        fetcher=lambda *_: (200, '<title>Home</title><a href="/cli">CLI</a>'))
    assert result['status'] == 'not_found_or_inconclusive'
    assert not result['candidates']


def test_foreign_hosts_credentials_and_non_https_are_rejected():
    for url in ('https://example.com.evil.org/cli', 'https://evil.org/cli',
                'http://example.com/cli', 'https://user@example.com/cli',
                'https://example.com:8443/cli'):
        assert safe_url(url, 'example.com') is None
    assert safe_url('https://docs.example.com/cli#one', 'example.com') == 'https://docs.example.com/cli'


def test_fetch_error_is_inconclusive():
    def failure(*_):
        raise OSError('unavailable')
    result = discover('https://example.com/', fetcher=failure)
    assert result['status'] == 'not_found_or_inconclusive'
    assert result['pages_visited'][0]['status'] == 'fetch_failed'


def test_same_domain_user_generated_page_is_not_asserted_official():
    pages = {
        'https://example.com/': '<a href="/random-user/cli">CLI</a>',
        'https://example.com/random-user/cli': '<title>Personal CLI project</title>',
    }
    result = discover('https://example.com/', fetcher=lambda url, host, timeout: (200, pages[url]))
    assert result['candidates'][0]['status'] == 'same_site_unverified_candidate'
    assert 'user-generated' in result['limitations'][0]
