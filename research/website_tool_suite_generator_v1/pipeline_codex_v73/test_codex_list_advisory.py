from codex_list_advisory import compare_prefix


def test_detects_extra_and_omitted_items_without_certifying():
    actual = ['First', 'Second', '.... unusual', 'Fourth']
    good = compare_prefix(actual, [actual], 5)
    assert good['status'] == 'consistent'
    extra = compare_prefix(actual + ['Archive'], [actual], 5)
    assert extra['status'] == 'mismatch' and extra['expected_count'] == 4
    omitted = compare_prefix(['First', 'Second', 'Fourth'], [actual], 5)
    assert omitted['status'] == 'mismatch'
    assert omitted['expected_items'][2] == '.... unusual'


def test_abstains_when_list_is_ambiguous_or_case_is_unsuitable():
    items = ['First', 'Second']
    assert compare_prefix(items, [items, items], 3)['status'] == 'ambiguous'
    assert compare_prefix(['First'], [items], 3)['status'] == 'inapplicable'
    assert compare_prefix(items, [items], 1)['status'] == 'inapplicable'
    assert compare_prefix(['same', 'same'], [items], 3)['status'] == 'ambiguous'
