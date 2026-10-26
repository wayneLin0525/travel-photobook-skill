"""Mechanical scan/review-protocol/retrieval benchmark; NOT visual curation.

Uses synthetic pictures and synthetic vectors, labels every review as test_fixture,
and never renders or delivers a user book. Run separately from ordinary pytest.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
import numpy as np
from PIL import Image
from photobook.core import Run,read,write
from photobook.images import scan
from photobook.review import dedup_proposal,prepare,submit,finish
from tests.fixtures import auto_answer,config

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',required=True)
    parser.add_argument('--sizes',nargs='+',type=int,default=[1000,3000]);args=parser.parse_args()
    work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True);reports=[]
    for count in args.sizes:
        assert 1<=count<=10000
        folder=work/str(count)
        if folder.exists():raise SystemExit('保留旧测试记录，请使用新工作目录。')
        source=folder/'photos';source.mkdir(parents=True)
        start=time.perf_counter();base=datetime(2026,1,1)
        for i in range(count):
            image=Image.new('RGB',(96,72),((i*17)%256,(i*31)%256,(i*43)%256))
            image.putpixel((0,0),(i%256,i//256,127))
            exif=image.getexif();exif[36867]=(base+timedelta(seconds=i*10)).strftime('%Y:%m:%d %H:%M:%S')
            exif[271]='Synthetic';exif[272]='Scale test'
            image.save(source/f'{i:05}.png',exif=exif)
        run=Run.create(source,folder/'job',config());t=time.perf_counter();scan(run);scan_seconds=time.perf_counter()-t
        t=time.perf_counter();batch_folder=prepare(run,'screen');batch=read(batch_folder/'batch.json')
        for task in batch['tasks']:
            path=batch_folder/f'{task["task_id"]}.fixture.json';write(path,auto_answer(run,task,{}));submit(run,batch_folder,path)
        finish(run,batch_folder);review_seconds=time.perf_counter()-t
        ids=run.snapshot('screen')['data']['active'];rng=np.random.default_rng(20261026)
        vectors=rng.standard_normal((len(ids),64)).astype('float32');vectors/=np.linalg.norm(vectors,axis=1,keepdims=True)
        matrix=run.root/'vectors.npz';np.savez(matrix,ids=np.array(ids),vectors=vectors)
        run.commit('embed',dict(matrix='vectors.npz',model={'test_fixture':True,'dimension':64}))
        t=time.perf_counter();proposal=dedup_proposal(run);retrieval_seconds=time.perf_counter()-t
        assert len(proposal['canonical'])+len(proposal['exact'])==count
        result=dict(count=count,scan_seconds=scan_seconds,synthetic_review_protocol_seconds=review_seconds,
                    retrieval_seconds=retrieval_seconds,total_seconds=time.perf_counter()-start,
                    screen_tasks=len(batch['tasks']),max_screen_targets=max(len(t['target_ids']) for t in batch['tasks']),
                    candidate_pairs=len(proposal['pairs']),lost_photos=0,model_inference=False,
                    visual_curation=False,delivery_allowed=False)
        write(folder/'benchmark.json',result);reports.append(result);print(json.dumps(result),flush=True)
    write(work/'benchmark.json',reports)

if __name__=='__main__':main()
