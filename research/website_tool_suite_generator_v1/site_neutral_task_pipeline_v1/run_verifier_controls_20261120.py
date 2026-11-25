"""Small known-answer visual smoke test; no real website, agent, or held tasks."""
import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from PIL import Image, ImageDraw, ImageFont
import visual_verifier as verifier

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')


def make_picture(path, lines):
    im = Image.new('RGB', (1000, 600), 'white')
    draw = ImageDraw.Draw(im)
    try:
        cjk = any(any('\u3400' <= c <= '\u9fff' for c in line) for line in lines)
        font = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if cjk else
                                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 30)
    except OSError:
        if cjk:
            raise RuntimeError('CJK font unavailable; refusing to create unreadable controls')
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((30, 30 + 65*i), line, fill='black', font=font)
    im.save(path)


def cases(out):
    screen = out/'catalog.png'
    partial = out/'partial.png'
    make_picture(screen, ['Catalog results', 'Amber lamp  $12', 'Blue chair  $16', 'Cedar desk  $24'])
    make_picture(partial, ['Catalog results', 'Amber lamp  $12', 'Blue chair  $16', '(remaining items not visible)'])
    task = {'task_id':'catalog', 'parameters':{'query':'furniture'},
            'intent':'Return the three displayed items and prices in ascending price order.'}
    contract = {'goal':task['intent'], 'returns':{'items':'list[object]'}}
    rows = [{'name':'Amber lamp','price':12},{'name':'Blue chair','price':16},{'name':'Cedar desk','price':24}]
    def answer(items):
        return {'parameters':task['parameters'], 'result':{'items':items}, 'source_url':'https://example.org/'}
    return [
        ('correct','pass',screen,answer(rows)),
        ('wrong_price','fail',screen,answer([rows[0],{'name':'Blue chair','price':19},rows[2]])),
        ('wrong_order','fail',screen,answer(list(reversed(rows)))),
        ('missing_evidence','inconclusive',partial,answer(rows)),
    ], contract, task


def main():
    out = ROOT/('verifier_visual_controls_20261120_'+uuid4().hex[:8])
    out.mkdir(mode=0o700)
    examples, contract, task = cases(out)
    results = []
    errors = 0
    for index, (name, expected, image, answer) in enumerate(examples):
        if errors >= 2:
            results.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            report = verifier.judge('https://example.org/',contract,task,answer,[image],out/f'case_{index:02d}',
                                    model='qwen3.5-flash',timeout_seconds=60)
            errors = errors+1 if report.get('error_type') else 0
            results.append({'case':name,'expected':expected,'status':report['status'],
                            'format':report['format']['status'],'error_type':report.get('error_type')})
        summary={'created_at':datetime.now(timezone.utc).isoformat(), 'cases':results,
                 'interpretation':'Tiny synthetic development controls, not a generalization estimate.',
                 'held_tasks_opened':False}
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(results[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__ == '__main__':
    main()
