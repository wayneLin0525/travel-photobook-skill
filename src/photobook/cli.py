import argparse
import json
import sys
from pathlib import Path
from .core import Run, WorkflowError, read, write

def main():
    if sys.platform=='win32' and hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description='旅行照片书 Skill 执行引擎')
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('doctor');q.add_argument('--source');q.add_argument('--prepare',action='store_true');q.add_argument('--probe-image')
    q=sub.add_parser('start');q.add_argument('source');q.add_argument('--output',required=True);q.add_argument('--work-root');q.add_argument('--config')
    for command in ('advance','deliver'):
        q=sub.add_parser(command);q.add_argument('run')
    q=sub.add_parser('submit-review');q.add_argument('run');q.add_argument('answer')
    q=sub.add_parser('revise');q.add_argument('run');q.add_argument('stage')
    q=sub.add_parser('init');q.add_argument('source');q.add_argument('run');q.add_argument('--config')
    for name in ['scan','status','verify','report','render']:
        q=sub.add_parser(name);q.add_argument('run')
    q=sub.add_parser('embed');q.add_argument('run');q.add_argument('--model-cache')
    q=sub.add_parser('locate');q.add_argument('run');q.add_argument('reviewed_locations')
    q=sub.add_parser('prepare');q.add_argument('run');q.add_argument('stage',choices=['screen','dedup','cluster','event_select','select','story','layout','qa'])
    q=sub.add_parser('submit');q.add_argument('run');q.add_argument('batch');q.add_argument('answer')
    q=sub.add_parser('finish');q.add_argument('run');q.add_argument('batch')
    q=sub.add_parser('diff');q.add_argument('run');q.add_argument('stage');q.add_argument('before');q.add_argument('after')
    q=sub.add_parser('restore');q.add_argument('run');q.add_argument('stage');q.add_argument('revision')
    q=sub.add_parser('configure');q.add_argument('run');q.add_argument('config')
    q=sub.add_parser('web');q.add_argument('run');q.add_argument('output');q.add_argument('--version',required=True);q.add_argument('--based-on');q.add_argument('--edge',type=int,default=1600)
    a=p.parse_args()
    try:
        if a.command in ('doctor','start','advance','submit-review','revise','deliver'):
            from . import driver
            if a.command=='doctor':
                if a.prepare:
                    from .assets import model_asset
                    model_asset()
                result=driver.doctor(a.source)
                if a.probe_image:
                    from .models import encode_paths
                    import numpy as np
                    vectors,provenance=encode_paths([Path(a.probe_image).expanduser().resolve()])
                    result['model_probe']=dict(model=provenance['model_id'],shape=list(vectors.shape),
                        finite=bool(np.isfinite(vectors).all()),normalised=bool(np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-3)))
            elif a.command=='start':result=driver.start(a.source,output=a.output,work_root=a.work_root,config=read(a.config) if a.config else None)
            elif a.command=='advance':result=driver.advance(a.run)
            elif a.command=='submit-review':result=driver.submit_review(a.run,a.answer)
            elif a.command=='revise':result=driver.revise(a.run,a.stage)
            else:result=driver.deliver_job(a.run)
            print(json.dumps(result,ensure_ascii=False,indent=2));return
        if a.command=='init':
            r=Run.create(a.source,a.run,read(a.config) if a.config else None);print(r.root);return
        run=Run(a.run)
        if a.command=='status':print(json.dumps(run.status(),ensure_ascii=False,indent=2));return
        if a.command=='verify':run.verify_sources();print('原图校验通过');return
        if a.command=='diff':print(json.dumps(run.diff(a.stage,a.before,a.after),ensure_ascii=False,indent=2));return
        with run.lock():
            if a.command=='web':
                from .web_export import export_web
                print(export_web(run,a.output,version=a.version,based_on=a.based_on,edge=a.edge));return
            if a.command=='restore':
                print(json.dumps(run.restore(a.stage,a.revision),ensure_ascii=False,indent=2))
                from .report import report
                report(run);return
            if a.command=='configure':
                config=dict(run.config);config.update(read(a.config));run.validate_config(config)
                old=run.root/'config-history'/f'{run.config_hash()}.json';write(old,run.config)
                write(run.root/'config.json',config);print('配置已更新；旧阶段将标为过期，请重新执行。');return
            if a.command=='scan':
                from .images import scan
                result=scan(run)
            elif a.command=='embed':
                from .models import embed
                result=embed(run,a.model_cache)
            elif a.command=='locate':
                from .locations import import_locations
                result=import_locations(run,a.reviewed_locations)
            elif a.command in ['prepare','submit','finish']:
                from .review import prepare,submit,finish
                if a.command=='prepare':result=prepare(run,a.stage)
                elif a.command=='submit':result=submit(run,a.batch,a.answer)
                else:result=finish(run,a.batch)
            elif a.command=='render':
                from .layout import render
                result=render(run)
            else:result=None
            from .report import report
            if 'scan' in run.heads():report(run)
            if isinstance(result,dict):print(json.dumps(dict(stage=result['stage'],revision=result['revision'],counts=result['data'].get('counts',{})),ensure_ascii=False))
            elif result:print(result)
            else:print(run.root/'report.html')
    except (WorkflowError,FileNotFoundError,json.JSONDecodeError) as e:
        print(json.dumps(dict(state='BLOCKED',error=str(e)),ensure_ascii=False));p.exit(2)

if __name__=='__main__':main()
