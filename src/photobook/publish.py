"""Atomic three-file delivery. All provenance remains in the private job directory."""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pymupdf as fitz

from .core import file_hash, now, require, write
from .web_export import build_reader
from .overview import build_overview

DELIVERY_FILENAMES = dict(pdf='照片书.pdf', html='照片书.html', overview='跨页总览.jpg')
LEGACY_FILENAMES = {DELIVERY_FILENAMES[k] for k in ('pdf', 'html')}


def verify_render(run):
    data = run.snapshot('render')['data']
    frozen = data.get('file_hashes', {})
    for name, digest in frozen.items():
        require(file_hash(run.root / name) == digest, '已审阅输出发生变化：' + name)
    paths = data.get('page_images', [])
    if paths:
        # Also supports old reviewed runs that predate per-artifact hashes.
        from PIL import Image
        expected = []
        for key in ('design_pdf', 'cover_pdf'):
            with fitz.open(run.root / data[key]) as doc:
                for page in doc:
                    pix = page.get_pixmap(dpi=110, colorspace=fitz.csRGB, alpha=False)
                    expected.append((pix.width, pix.height, pix.samples))
        require(len(paths) == len(expected), '审阅页面与实际PDF页数不一致')
        for name, (width, height, samples) in zip(paths, expected):
            with Image.open(run.root / name) as image:
                image = image.convert('RGB')
                require(image.size == (width, height) and image.tobytes() == samples,
                        'PDF与实际审阅页面不同：' + name)


def deliver(run, destination, *, version=None, edge=1600):
    destination = Path(destination).expanduser().resolve()
    require(not destination.exists(), '结果版本已存在，不能覆盖')
    for protected in (run.source.resolve(), run.root.resolve()):
        require(destination != protected and protected not in destination.parents and destination not in protected.parents,
                '成品目录必须位于原图和内部任务目录之外')
    require(not run.config.get('test_mode'), '合成测试任务不能作为用户成品发布')
    snapshots = {s: run.snapshot(s)['revision'] for s in ('story', 'layout', 'render', 'qa')}
    require(run.snapshot('qa')['data']['visual_status'] == 'passed' and
            not run.snapshot('qa')['data'].get('unresolved'), '必须先通过真实页面审阅')
    run.verify_sources(); verify_render(run)
    template, manifest = build_reader(run, version=version or destination.name, edge=edge)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.photobook-publish-', dir=destination.parent))
    try:
        pdf_path = staging / '照片书.pdf'
        data = run.snapshot('render')['data']
        with fitz.open() as combined:
            for key in ('cover_pdf', 'design_pdf'):
                with fitz.open(run.root / data[key]) as doc:
                    combined.insert_pdf(doc)
            combined.set_metadata({'title': run.config['title'], 'author': 'Travel Photo Book'})
            combined.save(pdf_path, garbage=4, deflate=True)
        (staging / '照片书.html').write_text(template, encoding='utf-8')
        with fitz.open(pdf_path) as doc:
            require(len(doc) == manifest['inner_pages'] + 1, '最终合订PDF页数错误')
        overview = build_overview(pdf_path, staging / DELIVERY_FILENAMES['overview'])
        require({p.name for p in staging.iterdir()} == set(DELIVERY_FILENAMES.values()), '成品目录必须恰好三个文件')
        run.verify_sources()
        require(all(run.snapshot(s)['revision'] == rev for s, rev in snapshots.items()), '发布期间审阅版本发生变化')
        manifest.update(published_at=now(), destination=str(destination), status='prepared', delivery_schema=2,
                        overview=overview,
                        final_files={p.name: dict(sha256=file_hash(p), bytes=p.stat().st_size) for p in staging.iterdir()})
        record = run.root / 'deliveries' / (uuid.uuid4().hex + '.json')
        write(record, manifest)
        require(not destination.exists(), '版本目录被其他任务占用')
        staging.rename(destination)
        manifest['status'] = 'published'; write(record, manifest)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {key: str(destination / name) for key, name in DELIVERY_FILENAMES.items()}
