"""Read tool result logs, including valid multi-line JSON results."""
import json
from pathlib import Path
import re


def read_result(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    if not re.match(r'^step 0 action: (?:start )?params:', text):
        raise ValueError('missing first-line parameter record')
    matches = list(re.finditer(r'^(?:final result:|step \d+ action: final_result params:)\s*',
                               text, flags=re.MULTILINE))
    if not matches:
        raise ValueError('missing final result')
    value = json.loads(text[matches[-1].end():])
    if not isinstance(value, dict):
        raise ValueError('final result is not an object')
    return value
