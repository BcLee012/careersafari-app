"""用户授权的六链接实际验证；独立数据库，真实模型，不覆盖旧评估结果。"""
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import db, fetcher, ingestion, profile


def run(output):
    logging.disable(logging.CRITICAL)
    root=Path(__file__).resolve().parents[2]
    previous=root/'docs/evaluations/2026-10-11-real-jd'
    output.mkdir(parents=True,exist_ok=True)
    db.DB_PATH=str((output/'evaluation.db').resolve());db.init_db()
    samples=[{'key':'baidu-growth','name':'百度：用户增长数据分析实习',
              'url':'https://talent.baidu.com/jobs/detail/INTERN/a361b79c-86e5-4295-8bdd-a1f9c9988a5d'}]
    samples += [{k:s[k] for k in ('key','name','url')} for s in json.loads((previous/'samples.json').read_text())]
    def collect(s):
        started=time.monotonic()
        doc,error,warnings=fetcher.fetch_document(s['url'])
        return s,doc,error,warnings,round(time.monotonic()-started,3)
    results=[]
    # Independent network reads in parallel; the ingestion service remains sequential.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for sample,doc,error,warnings,elapsed in pool.map(collect,samples):
            db.save_capture(sample['url'],doc,error)
            data={**sample,'collection_seconds':elapsed,'warnings':warnings}
            if not doc:
                data.update({'result':{'ok':False,'state':'fetch_failed','reasons':[error]},'audit':None})
                print(sample['name'],'采集失败',error,flush=True)
            else:
                (output/(sample['key']+'-source.txt')).write_text(doc['raw_text'])
                (output/(sample['key']+'-source.html')).write_text(doc['html'])
                data['source_metadata']=doc['metadata']
                print(sample['name'],'来源采集完成，开始实际模型检查',flush=True)
                result=ingestion.ingest(doc['raw_text'],sample['url'],'url_fetch',user_id='real-jd-repair-evaluation')
                data.update({'result':result,'audit':db.get_assessment(result['jd_id'])})
                print(sample['name'],result['state'],result['reasons'],flush=True)
            (output/(sample['key']+'-result.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
            results.append(data)
    (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    # Exercise the actual report function with explicitly synthetic student input.
    from careersafari.main import build_advice_for
    answers={'persona':'研一 · 方向未定型','major':'管理科学与工程','grade':'研一',
             'attained_degree':'本科','target_stage':'实习','direction_interest':'大数据分析与决策',
             'skills':[{'name':'SQL 取数','column':'transferable','evidence_source':'self_report',
                        'evidence_text':'模拟输入：学习过 SQL，没有提交作品。'}]}
    student=profile.build_profile('synthetic-repaired-jd',answers)
    advice=build_advice_for(student,answers)
    (output/'student-report.json').write_text(json.dumps({'synthetic_profile':True,'answers':answers,
                                                        'advice':advice},ensure_ascii=False,indent=2))
    assert not advice['positions'], '单一企业样本不得形成正式方向推荐'
    states={k:sum(d['result']['state']==k for d in results) for k in ('ready','review','excluded','fetch_failed')}
    print('实际验证结果',states,flush=True)


if __name__=='__main__':
    run(Path(sys.argv[1]).resolve())
