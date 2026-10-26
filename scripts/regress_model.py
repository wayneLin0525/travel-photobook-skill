"""Compare retrieval to recorded visual dedup decisions, without rerunning old weights."""
import argparse
import time
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from photobook.core import Run, write
from photobook.models import load_model
from photobook.images import open_photo
from photobook.review import cluster_proposal


def main():
    parser=argparse.ArgumentParser();parser.add_argument('runs',nargs='+');parser.add_argument('--output',required=True)
    args=parser.parse_args();import torch
    model,preprocess,provenance=load_model();report={};started=time.perf_counter()
    for path in args.runs:
        run=Run(path);photos=run.photos();ids=run.snapshot('screen')['data']['active'];vectors=[]
        for i,pid in enumerate(ids):
            with torch.inference_mode():
                v=model.encode_image(preprocess(open_photo(run.source/photos[pid]['path'])).unsqueeze(0))
                v=(v/v.norm(dim=-1,keepdim=True).clamp(min=1e-8))[0].cpu().numpy().astype('float32')
            vectors.append(v)
            if i%25==0:print(run.root.name,i+1,len(ids),flush=True)
        matrix=np.stack(vectors);scores=matrix@matrix.T;np.fill_diagonal(scores,-2)
        merges=[d for d in run.snapshot('dedup')['decisions'] if d['action']=='merge' and d['photo_id'] in ids and d.get('related_photo_ids') and d['related_photo_ids'][0] in ids]
        ranks=[]
        for d in merges:
            i=ids.index(d['photo_id']);j=ids.index(d['related_photo_ids'][0]);r=int(np.flatnonzero(np.argsort(-scores[i],kind='stable')==j)[0])+1
            ranks.append(dict(photo_id=d['photo_id'],representative=d['related_photo_ids'][0],rank=r,similarity=float(scores[i,j]),reason=d['reason_code']))
        dest=Path(args.output).parent/(run.root.name+'-clip-vectors.npz');dest.parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(dest,ids=np.array(ids),vectors=matrix)
        report[run.root.name]=dict(photos=len(ids),dimension=matrix.shape[1],finite=bool(np.isfinite(matrix).all()),
            compared_reviewed_merges=len(ranks),recall_at_5=sum(r['rank']<=5 for r in ranks)/len(ranks) if ranks else None,
            recall_at_10=sum(r['rank']<=10 for r in ranks)/len(ranks) if ranks else None,ranks=ranks,
            limitation='Compares candidate retrieval only; no automatic merge or aesthetic judgment.')
    write(args.output,dict(model=provenance,elapsed_seconds=time.perf_counter()-started,datasets=report))

if __name__=='__main__':main()
