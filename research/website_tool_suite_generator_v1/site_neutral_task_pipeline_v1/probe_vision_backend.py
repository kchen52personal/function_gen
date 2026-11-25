"""Two short, spend-logged API probes: identical model with and without image."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import visual_verifier as v

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
OUT=ROOT/'visual_backend_probe_20261117_v1'
IMAGE=ROOT/'visual_verifier_trials_20261117_v1/control_images/complete.png'


def probe(label):
    out=OUT/label;out.mkdir()
    prompt='Reply with exactly the word READY. Do not explain.'
    parts=[{'type':'text','text':prompt}]
    manifest=[]
    if label=='with_image':
        imageparts,manifest=v._image_parts([IMAGE]);parts.extend(imageparts)
    payload={'model':'qwen3.5-flash','messages':[{'role':'user','content':parts}],
             'max_tokens':64,'temperature':0,'stream':False}
    start=time.monotonic()
    try:
        response=v.model_request(payload,out,manifest,timeout_seconds=30)
        return {'probe':label,'status':'response','elapsed_seconds':round(time.monotonic()-start,2),
                'text':v._response_text(response)[:150]}
    except Exception as exc:
        return {'probe':label,'status':'api_error','error_type':type(exc).__name__,
                'elapsed_seconds':round(time.monotonic()-start,2)}


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records=list(pool.map(probe,['text_only','with_image']))
    (OUT/'summary.json').write_text(json.dumps({'probes':records},indent=2)+'\n')
    print(json.dumps(records))

if __name__=='__main__':main()
