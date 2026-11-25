"""Treat website text as literal data when embedded in a Jinja template."""
import re


def escape_jinja_literal(value: str) -> str:
    replacements = {
        '{{': "{{ '{{' }}",
        '{%': "{{ '{%' }}",
        '{#': "{{ '{#' }}",
    }
    return re.sub(r'\{\{|\{%|\{#', lambda match: replacements[match.group(0)], value)
