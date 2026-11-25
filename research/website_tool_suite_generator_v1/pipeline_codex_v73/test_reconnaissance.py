from reconnaissance import _route_family, _candidate_links, _candidate_priority, _safe_first_party


def test_category_values_share_one_route_family() -> None:
    chinese = "https://music.163.com/discover/playlist/?cat=%E5%8D%8E%E8%AF%AD"
    electronic = "https://music.163.com/discover/playlist/?cat=%E7%94%B5%E5%AD%90"
    assert _route_family(chinese) == _route_family(electronic)


def test_content_ids_share_one_route_family() -> None:
    assert _route_family("https://music.163.com/song?id=1") == _route_family(
        "https://music.163.com/song?id=999"
    )


def test_trailing_slash_does_not_create_a_new_family() -> None:
    assert _route_family("https://music.163.com/discover/playlist") == _route_family(
        "https://music.163.com/discover/playlist/"
    )


def test_discovery_filters_are_arguments_of_one_family() -> None:
    assert _route_family("https://music.163.com/discover/toplist") != _route_family(
        "https://music.163.com/discover/toplist?id=19723756"
    )
    assert _route_family("https://music.163.com/discover/playlist") != _route_family(
        "https://music.163.com/discover/playlist?cat=rock&order=hot"
    )


def test_different_query_shapes_remain_distinct() -> None:
    assert _route_family("https://example.test/items?category=a") != _route_family(
        "https://example.test/items?category=a&sort=hot"
    )


def test_text_paths_remain_distinct_on_any_site() -> None:
    assert _route_family("https://example.test/articles/alpha") != _route_family(
        "https://example.test/articles/beta"
    )


def test_wikipedia_main_page_and_articles_are_distinct_families() -> None:
    assert _route_family("https://en.wikipedia.org/wiki/Main_Page") != _route_family(
        "https://en.wikipedia.org/wiki/Ada_Lovelace"
    )


def test_numeric_identifiers_are_normalized_on_any_site() -> None:
    assert _route_family('https://example.test/items/123') == _route_family('https://example.test/items/456')
    assert _route_family('https://other.test/items/123') == _route_family('https://other.test/items/456')


def test_labelled_links_precede_unlabelled_links_without_discarding_them() -> None:
    page = {'final_url': 'https://example.test/', 'frames': [{'links': [
        {'href': '/unlabelled', 'text': '', 'order': 0, 'in_primary_chrome': True},
        {'href': '/search', 'text': 'Search', 'order': 1, 'in_primary_chrome': True},
        {'href': '/image', 'image_alt': 'Browse articles', 'order': 2},
        {'href': '/accessible', 'aria_label': 'Browse by subject', 'order': 3},
    ]}]}
    links = sorted(_candidate_links(page, 'example.test'), key=_candidate_priority)
    assert [link['url'] for link in links] == [
        'https://example.test/search', 'https://example.test/image',
        'https://example.test/accessible', 'https://example.test/unlabelled']


def test_sensitive_label_remains_lower_priority_than_unlabelled_link() -> None:
    base = {'in_primary_chrome': True, 'order': 0}
    assert _candidate_priority({**base, 'url': 'https://example.test/', 'text': ''}) < _candidate_priority(
        {**base, 'url': 'https://example.test/login', 'aria_label': 'Sign in'})


def test_same_page_anchors_are_not_separate_pages() -> None:
    assert _safe_first_party('https://example.test/', '#content', 'example.test') == 'https://example.test/'
    assert _safe_first_party('https://example.test/', '/articles#section', 'example.test') == 'https://example.test/articles'


def test_submission_link_is_deprioritized_for_read_only_recon() -> None:
    base = {'in_primary_chrome': True, 'order': 0}
    assert _candidate_priority({**base, 'url': 'https://example.test/search', 'text': 'Search'}) < _candidate_priority(
        {**base, 'url': 'https://example.test/submit', 'text': 'Submit'})


def test_cookie_policy_is_lower_priority_than_task_navigation() -> None:
    base = {'in_primary_chrome': True, 'order': 0}
    assert _candidate_priority({**base, 'url': 'https://example.test/browse', 'text': 'Browse topics'}) < _candidate_priority(
        {**base, 'url': 'https://example.test/cookies', 'text': 'Cookies'})
