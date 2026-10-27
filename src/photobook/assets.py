"""Pinned, resumable downloads. No credentials, mirrors, or mutable model tags."""
from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

from .core import file_hash, read, require, WorkflowError
from .runtime import RESOURCES, cache_root


def download(url, destination, sha256, *, retries=3):
    require(url.startswith('https://'), '资源只允许HTTPS下载')
    require(len(sha256) == 64 and all(c in '0123456789abcdef' for c in sha256), '资源缺少固定SHA-256')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock,Timeout
    try:
        with FileLock(str(destination)+'.download.lock',timeout=0):
            return _download_locked(url,destination,sha256,retries=retries)
    except Timeout as error:
        raise WorkflowError('同一资源正在另一个任务中准备，请稍后继续：'+destination.name) from error


def _download_locked(url,destination,sha256,*,retries):
    if destination.exists():
        require(file_hash(destination) == sha256, '缓存资源损坏，拒绝使用：' + destination.name)
        return destination
    partial = destination.with_suffix(destination.suffix + '.part')
    last = None
    for attempt in range(retries):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {'User-Agent': 'travel-photobook/0.2.2', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
                resumed = offset and response.status == 206
                if resumed:
                    require(response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'), '下载续传位置不正确')
                with partial.open('ab' if resumed else 'wb') as handle:
                    while block := response.read(1024 * 1024):
                        handle.write(block)
                    handle.flush(); os.fsync(handle.fileno())
            require(file_hash(partial) == sha256, '下载资源校验失败：' + destination.name)
            os.replace(partial, destination)
            return destination
        except (OSError, WorkflowError) as error:
            if os.environ.get('PHOTOBOOK_DEBUG') == '1':
                import traceback
                traceback.print_exc()
            last = error
            if isinstance(error, WorkflowError):
                partial.unlink(missing_ok=True)
    raise WorkflowError(f'自动下载失败，已保留可续传部分：{destination.name}；{last}')


def model_asset(cache=None):
    spec = read(RESOURCES / 'assets.json')['model']
    root = Path(cache) if cache else cache_root() / 'models' / spec['id']
    provenance=root/'model-provenance.json'
    if provenance.exists():
        require(read(provenance).get('model_id')==spec['id'],
                '此目录属于旧模型，不能覆盖其来源记录；请使用新的模型缓存目录')
    return download(spec['url'], root / spec['filename'], spec['sha256']), spec
