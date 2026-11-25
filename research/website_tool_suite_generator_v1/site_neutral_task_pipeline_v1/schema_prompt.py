"""Contract-specific output guidance; no task answers or held inputs required."""
import json


def result_guidance(returns):
    """Describe the exact result object, with typed illustrative placeholders."""
    examples = {
        'str': '<text>', 'string': '<text>', 'url': 'https://example.invalid/',
        'int': 0, 'integer': 0, 'number': 0, 'float': 0,
        'bool': False, 'boolean': False, 'object': {},
        'list[str]': [], 'list[object]': [],
    }
    if not isinstance(returns, dict) or not returns:
        raise ValueError('return schema must be a nonempty object')
    try:
        illustration = {name: examples[kind] for name, kind in returns.items()}
    except (KeyError, TypeError) as exc:
        raise ValueError('unsupported return schema type') from exc
    return ('The result must be an object with exactly these keys and types: '
            + json.dumps(returns, ensure_ascii=False)
            + '. Structural illustration only, not an answer: '
            + json.dumps({'result': illustration}, ensure_ascii=False)
            + '. Replace all illustrative values with the actual requested data; '
              'never return the illustration as the answer. A list belongs inside its named field, '
              'not directly in result.')
