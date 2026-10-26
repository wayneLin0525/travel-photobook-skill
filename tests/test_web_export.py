import base64
import hashlib
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path

import pymupdf as fitz
import pytest

from photobook.core import WorkflowError, file_hash, read
from photobook.web_export import chapter_links, export_web, safe_json, surfaces


class ReviewedRun:
    """A tiny reviewed PDF fixture; no vision model or network needed to test export."""
    def __init__(self, root):
        self.root = root / 'run'; self.root.mkdir()
        self.source = root / 'photos'; self.source.mkdir()
        self.info = {'run_id': 'fixture'}
        self.config = {'title': '</script><script>alert(1)</script>'}
        self.checked = False
        pages = [dict(number=i, title=f'页{i}', items=[dict(photo_id=f'P{i}')]) for i in range(1, 5)]
        for name, count in [('cover.pdf', 1), ('inner.pdf', 4)]:
            with fitz.open() as doc:
                for i in range(count):
                    page = doc.new_page(width=210, height=210)
                    page.insert_text((20, 60), f'{name} {i+1}')
                doc.save(self.root / name)
        self.data = {
            'story': {'chapters': [{'title': '第一章', 'photo_ids': ['P1','P2']}, {'title': '第二章', 'photo_ids': ['P3','P4']}]},
            'layout': {'pages': pages},
            'render': {'cover_pdf': 'cover.pdf', 'design_pdf': 'inner.pdf'},
            'qa': {'visual_status': 'passed', 'unresolved': []}}
    def verify_sources(self): self.checked = True
    def snapshot(self, stage): return dict(revision=stage+'-rev', data=deepcopy(self.data[stage]))


def test_real_book_parity_and_chapter_mapping():
    pages = surfaces(32)
    assert len(pages) == 36
    assert [p['kind'] for p in pages[:3]] == ['cover', 'blank', 'page']
    assert pages[2]['pdf_page'] == 1
    spreads = [pages[i:i+2] for i in range(1, 35, 2)]
    assert [p['pdf_page'] for p in spreads[1]] == [2, 3]
    assert [p['pdf_page'] for p in spreads[-1]] == [32, None]
    assert pages[35]['kind'] == 'back'
    assert chapter_links([{'title':'甲','photo_ids':['X']}],[{'number':7,'items':[{'photo_id':'X'}]}]) == [{'title':'甲','page':7,'index':8}]
    with pytest.raises(WorkflowError): chapter_links([{'title':'甲','photo_ids':['missing']}],[])


def test_offline_export_is_reproducible_traceable_and_portable(tmp_path):
    run = ReviewedRun(tmp_path); dest = tmp_path / 'V3'
    before = {p.name:file_hash(p) for p in run.root.glob('*.pdf')}
    path = export_web(run,dest,version='V3',based_on='V2',edge=800)
    assert run.checked
    text = path.read_text()
    assert '</script><script>alert(1)</script>' not in text
    data = json.loads(re.search(r'<script type="application/json" id="book-data">(.*?)</script>', text, re.S).group(1))
    assert data['title'] == run.config['title']
    assert 'Permission is hereby granted, free of charge' in text
    manifest = read(dest/'版本清单.json')
    assert manifest['version'] == 'V3' and manifest['based_on_version'] == 'V2'
    assert manifest['revisions']['qa'] == 'qa-rev'
    assert manifest['source_pdfs']['design_pdf']['sha256'] == before['inner.pdf']
    assert manifest['content_pages'] == 5 and manifest['inner_pages'] == 4
    assert manifest['files']['dist/index.html']['sha256'] == file_hash(path)
    assert str(run.source) not in text
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',text)
    for p in data['pages']:
        if p.get('image'):
            raw = base64.b64decode(p['image'].split(',',1)[1])
            assert hashlib.sha256(raw).hexdigest() == p['raster']['sha256']
            with fitz.open(run.root/('cover.pdf' if p['kind']=='cover' else 'inner.pdf')) as doc:
                pix = doc[p['pdf_page']-1].get_pixmap(matrix=fitz.Matrix(800/210,800/210),alpha=False,colorspace=fitz.csRGB)
                assert pix.tobytes('jpeg',jpg_quality=90) == raw
    with zipfile.ZipFile(dest/'照片书网页预览.zip') as archive:
        assert archive.read('dist/index.html') == path.read_bytes()
        assert 'dist/LICENSE.page-flip.txt' in archive.namelist()
        assert archive.testzip() is None
    assert {p.name:file_hash(p) for p in run.root.glob('*.pdf')} == before
    with pytest.raises(WorkflowError, match='已存在'): export_web(run,dest,version='V3')


def test_export_blocks_unreviewed_wrong_geometry_and_source_write(tmp_path):
    run = ReviewedRun(tmp_path)
    with pytest.raises(WorkflowError, match='目录之外'): export_web(run,run.source/'V3',version='V3')
    run.data['qa']['visual_status'] = 'issues'
    with pytest.raises(WorkflowError, match='审阅'): export_web(run,tmp_path/'bad',version='V3')
    run.data['qa']['visual_status'] = 'passed'
    run.data['qa']['unresolved'] = ['failed page']
    with pytest.raises(WorkflowError, match='审阅'): export_web(run,tmp_path/'bad',version='V3')
    run.data['qa']['unresolved'] = []
    with fitz.open() as doc:
        doc.new_page(width=300,height=200); doc.save(run.root/'cover.pdf')
    with pytest.raises(WorkflowError, match='尺寸不同'): export_web(run,tmp_path/'bad',version='V3')
    assert not (tmp_path/'bad').exists()
