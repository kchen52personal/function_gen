"""Offline advisory scan for supplied example values in a generated Python candidate.

Not a safety boundary or proof of generalization. No task set is read implicitly;
callers must supply development-only values or an explicitly permitted test inventory.
Never include held-out requests or answers in a function-generation prompt.
"""
import argparse
import ast
import json
import unicodedata
from pathlib import Path


def norm(value):
    return unicodedata.normalize('NFKC', value).casefold()


def scan(code, values):
    tree = ast.parse(code)
    terms = [(v, norm(v).strip()) for v in values if isinstance(v, str)
             and (len(norm(v).strip()) >= 4 or
                  (len(norm(v).strip()) >= 2 and any(c.isdigit() for c in v)))]
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        literal = norm(node.value)
        for original, term in terms:
            if term in literal:
                findings.append({'line': node.lineno, 'matched_value': original,
                                 'match': 'exact' if literal == term else 'embedded',
                                 'literal': node.value[:500]})
    return {'findings': sorted(findings, key=lambda x:(x['line'],x['matched_value'])),
            'status':'review_required' if findings else 'no_literal_match',
            'limitations':['Matches may be harmless; manual review is needed.',
                           'No match does not exclude hard-coding or data leakage.',
                           'Dynamic, encoded, transformed, and external values may not be detected.',
                           'Supplied inventory is not evidence of a held-out split.']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate', type=Path)
    p.add_argument('values_json',type=Path,help='JSON array of permitted development example values')
    p.add_argument('report',type=Path)
    args=p.parse_args()
    values=json.loads(args.values_json.read_text(encoding='utf-8'))
    if not isinstance(values,list) or not all(isinstance(x,str) for x in values):
        raise ValueError('values must be an array of strings')
    report=scan(args.candidate.read_text(encoding='utf-8'),values)
    with args.report.open('x',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
        f.write('\n')
    print(json.dumps({'status':report['status'],'findings':len(report['findings'])}))

if __name__=='__main__': main()
