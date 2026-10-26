from __future__ import annotations
import os
from pathlib import Path
import numpy as np
from .core import file_hash, fingerprint, write, require, environment
from .images import open_photo
from .assets import model_asset

MODEL = 'ViT-B-32-quickgelu'
PRETRAINED = 'openai'


def load_model(cache=None):
    import torch
    import open_clip
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    weights, spec = model_asset(cache)
    model, _, preprocess = open_clip.create_model_and_transforms(
        spec['architecture'], pretrained=str(weights), device='cpu', weights_only=False)
    model.eval()
    provenance = dict(model=spec['architecture'], model_id=spec['id'], pretrained=spec['pretrained'],
                      backend='cpu', source=spec['url'], license=spec['license'],
                      weights=[dict(filename=weights.name, sha256=file_hash(weights))],
                      preprocess=str(preprocess), environment=environment())
    write(weights.parent / 'model-provenance.json', provenance)
    return model, preprocess, provenance


def encode_paths(paths, cache=None):
    import torch
    model, preprocess, provenance = load_model(cache)
    vectors = []
    for path in paths:
        with torch.inference_mode():
            v = model.encode_image(preprocess(open_photo(path)).unsqueeze(0))
            v = v / v.norm(dim=-1, keepdim=True).clamp(min=1e-8)
        vectors.append(v[0].cpu().numpy().astype('float32'))
    return np.stack(vectors), provenance


def embed(run, model_cache=None, *, limit=None):
    """Bound work per advance; vectors survive interruptions before stage commit."""
    run.verify_sources()
    ids = run.snapshot('screen')['data']['active']
    require(ids, '没有可分析照片')
    import torch
    model, preprocess, provenance = load_model(model_cache)
    key = fingerprint(dict(model=provenance['model_id'], weights=provenance['weights'],
                           preprocess=provenance['preprocess'], environment=provenance['environment']))
    photos = run.photos(); vectors = []; computed = 0
    for i, pid in enumerate(ids):
        p = photos[pid]; path = run.root / 'cache/vectors' / key / (p['sha256'] + '.npy')
        if path.exists():
            v = np.load(path, allow_pickle=False)
        else:
            if limit is not None and computed >= limit:
                return dict(pending=True, completed=i, total=len(ids))
            require(file_hash(run.source / p['path']) == p['sha256'], '编码前源文件已变化：' + pid)
            with torch.inference_mode():
                v = model.encode_image(preprocess(open_photo(run.source / p['path'])).unsqueeze(0))
                v = (v / v.norm(dim=-1, keepdim=True).clamp(min=1e-8))[0].cpu().numpy().astype('float32')
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            with tmp.open('wb') as f:
                np.save(f, v)
            tmp.replace(path); computed += 1
        require(np.isfinite(v).all() and abs(np.linalg.norm(v)-1) < 1e-3, f'无效向量：{pid}')
        vectors.append(v)
        if limit is None and i % 25 == 0:
            print(f'向量分析 {i+1}/{len(ids)}', flush=True)
    matrix = np.stack(vectors)
    target = run.root / 'cache' / ('matrix-' + fingerprint([ids, key]) + '.npz')
    np.savez_compressed(target, ids=np.array(ids), vectors=matrix)
    run.verify_sources()
    return run.commit('embed', dict(ids=ids, matrix=str(target.relative_to(run.root)), model=provenance))
