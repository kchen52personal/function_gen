"""Chinese synthetic visual checks, not real account data or independent evaluation."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v


def main():
    out=graphics.ROOT/('verifier_chinese_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    visual_sha=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    cases=[
      ('calendar_correct','pass',['会议详情','主题：项目讨论','时间：周二 10:00','地点：会议室 A'],
       '返回会议主题、时间和地点。',{'title':'项目讨论','time':'周二 10:00','place':'会议室 A'}),
      ('calendar_wrong_place','fail',['会议详情','主题：项目讨论','时间：周二 10:00','地点：会议室 A'],
       '返回会议主题、时间和地点。',{'title':'项目讨论','time':'周二 10:00','place':'会议室 B'}),
      ('delivery_hidden','inconclusive',['物流详情','商品：茶杯','状态：运输中','送达日期：未显示'],
       '返回商品、状态和送达日期。',{'item':'茶杯','status':'运输中','arrival':'周三'}),
      ('filter_wrong','fail',['收件箱','未读：会议通知','已读：账单'],
       '只返回屏幕上未读消息的标题。',{'titles':['会议通知','账单']}),
    ]
    rows=[]; errors=0
    for i,(name,expected,lines,intent,fields) in enumerate(cases):
        if errors>=2: rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{key:'list[str]' if isinstance(value,list) else 'str' for key,value in fields.items()}}
            answer={'parameters':{},'result':fields,'source_url':'https://example.org/'}
            report=v.judge('https://example.org/',contract,task,answer,[image],out/f'case_{i:02d}',
                            model='qwen3.5-flash',timeout_seconds=75)
            error=report.get('error_type');errors=errors+1 if error else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'format':report['format']['status'],'error':error,'elapsed_seconds':report['elapsed_seconds']})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
           'cases':rows,'visual_sha256':visual_sha,'held_data_opened':False,
           'limitations':['Four hand-authored Chinese synthetic tasks; not a generalization benchmark.']},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    print('OUTPUT='+str(out),flush=True)


if __name__=='__main__':main()
