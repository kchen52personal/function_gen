from jinja2 import StrictUndefined, Template

from codex_prompt_escape import escape_jinja_literal


def test_website_template_syntax_remains_literal_and_cannot_execute():
    original = 'Text {{ value: bad }} {% for x in secret %}{{ x }}{% endfor %} {# note #}'
    rendered = Template(escape_jinja_literal(original), undefined=StrictUndefined).render()
    assert rendered == original
