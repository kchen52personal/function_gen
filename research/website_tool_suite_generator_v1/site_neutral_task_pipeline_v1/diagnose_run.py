#!/usr/bin/env python3
"""Read-only timing and aggregate split-similarity diagnostics.

Never print held-out inputs, and never pass them to a model. These checks are
not part of the production selection or function-generation path.
"""
import argparse
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re

from api_timing import summarize
from process_identity import inspect_identity


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(params):
    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(',', ':')).casefold()


def _normalized(params):
    return ' '.join(re.findall(r'\w+', _canonical(params), flags=re.UNICODE))


def inspect_diversity(root):
    state = _read(root/'summary.json')
    dev_path, held_path = root/'tasks/development/tasks.json', root/'tasks/held_out/tasks.json'
    seals = state['split_hashes']
    if _sha(dev_path) != seals['development'] or _sha(held_path) != seals['held_out']:
        raise ValueError('split manifest hash mismatch')
    development, held = _read(dev_path)['tasks'], _read(held_path)['tasks']
    replacements = []
    for file in sorted((root/'replacements').glob('round_*/tasks.json')):
        replacements.extend(_read(file)['tasks'])
    development += replacements
    exact = high = moderate = 0
    highest = 0.0
    for a in development:
        for b in held:
            left, right = _canonical(a['parameters']), _canonical(b['parameters'])
            exact += left == right
            ratio = SequenceMatcher(None, _normalized(a['parameters']), _normalized(b['parameters'])).ratio()
            highest = max(highest, ratio)
            high += ratio >= 0.85
            moderate += 0.65 <= ratio < 0.85
    return {'initial_development_count':len(development)-len(replacements),
            'replacement_count':len(replacements), 'held_count':len(held),
            'split_manifest_hashes_match':True, 'exact_cross_split_parameter_matches':exact,
            'lexically_high_similarity_pairs':high, 'lexically_moderate_similarity_pairs':moderate,
            'highest_normalized_character_similarity':round(highest,3),
            'parameters_disclosed':False,
            'limitation':'Lexical similarity is heuristic, not semantic equivalence; distinct values may exercise the same workflow, and paraphrases may be missed. Replacement files have no manifest hash here.'}


def inspect_timing(root):
    state = _read(root/'summary.json')
    batches=[]
    for path in sorted((root/'development_rounds').glob('round_*/summary.json')):
        batch = _read(path)
        attempts = batch.get('attempts',[])
        batches.append({'round':path.parent.name,'status':batch.get('status'),
            'attempts_recorded':len(attempts),
            'submitted':sum(x.get('trajectory_status')=='Submitted' and x.get('process_exit')==0 for x in attempts),
            'timed_out':sum(x.get('process_exit')==124 for x in attempts),
            'inner_budget_exhausted':sum(x.get('inner_tool_budget_exhausted') is True for x in attempts),
            'current':batch.get('current'),
            'attempts':[{'task_id':x.get('task_id'),'elapsed_seconds':x.get('elapsed_seconds'),
                         'agent_calls':x.get('agent_calls'),'inner_tool_calls':x.get('inner_tool_calls'),
                         'process_exit':x.get('process_exit'),'trajectory_status':x.get('trajectory_status')} for x in attempts]})
    return {'pipeline_status':state.get('status'),'started_at':state.get('started_at'),
            'process_visibility':inspect_identity(state.get('process_identity')),
            'batches':batches,'logged_api_timing':summarize(root),
            'limitation':'Agent/inner API logs only; elapsed time for older or unfinished attempts can be unavailable. Requests may overlap; failures can have unreported usage.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('diversity','timing'))
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    data = inspect_diversity(args.run) if args.mode=='diversity' else inspect_timing(args.run)
    print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
