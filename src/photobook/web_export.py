"""Offline reader export from reviewed PDF pages. Never changes workflow heads."""
from __future__ import annotations

import base64
import html
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pymupdf as fitz

from .core import file_hash, now, require, write, implementation_hashes

ASSETS = Path(__file__).parent / 'web'


def surfaces(pages):
    """Front cover; inside cover/p1; p2/p3; ...; pN/inside cover; back."""
    require(pages >= 2 and pages % 2 == 0, '翻页预览要求偶数内页')
    return ([dict(kind='cover', label='封面', pdf_page=1),
             dict(kind='blank', label='封面内侧', pdf_page=None)] +
            [dict(kind='page', label=f'{n:02}', pdf_page=n) for n in range(1, pages + 1)] +
            [dict(kind='blank', label='封底内侧', pdf_page=None),
             dict(kind='back', label='封底', pdf_page=None)])


def chapter_links(chapters, pages):
    links = []
    for chapter in chapters:
        ids = set(chapter['photo_ids'])
        hits = [p['number'] for p in pages if any(i['photo_id'] in ids for i in p['items'])]
        require(bool(hits), f'章节没有对应排版页面：{chapter["title"]}')
        links.append(dict(title=chapter['title'], page=min(hits), index=min(hits) + 1))
    return links


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def raster(page, edge):
    pix = page.get_pixmap(matrix=fitz.Matrix(edge / max(page.rect.width, page.rect.height), edge / max(page.rect.width, page.rect.height)), alpha=False, colorspace=fitz.csRGB)
    data = pix.tobytes('jpeg', jpg_quality=90)
    import hashlib
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode(), dict(width=pix.width, height=pix.height, sha256=hashlib.sha256(data).hexdigest())


def build_reader(run, *, version, based_on=None, edge=1600):
    require(800 <= edge <= 2400, '网页页面长边需在 800–2400 像素之间')
    require(bool(version and version.strip()), '请填写结果版本号')
    snaps = {s: run.snapshot(s) for s in ('story', 'layout', 'render', 'qa')}
    require(snaps['qa']['data']['visual_status'] == 'passed' and not snaps['qa']['data'].get('unresolved'), '必须先完成并通过页面审阅，才能导出网页')
    run.verify_sources()
    layout = snaps['layout']['data']; render = snaps['render']['data']
    pdfs = {k: run.root / render[k] for k in ('cover_pdf', 'design_pdf')}
    vendor = ASSETS / 'vendor'
    provenance = json.loads((vendor / 'provenance.json').read_text(encoding='utf-8'))
    for name, digest in provenance['files'].items():
        require(file_hash(vendor / name) == digest, f'翻页组件校验失败：{name}')
    records = surfaces(len(layout['pages']))
    with fitz.open(pdfs['cover_pdf']) as cover, fitz.open(pdfs['design_pdf']) as inner:
        require(len(cover) == 1 and len(inner) == len(layout['pages']), 'PDF 页数与已审阅排版不一致')
        rect = inner[0].rect
        require(all(abs(p.rect.width - rect.width) < .1 and abs(p.rect.height - rect.height) < .1 for doc in (cover, inner) for p in doc), '封面和内页尺寸不同，不能直接组成翻页书')
        for index, record in enumerate(records):
            record['index'] = index
            if record['kind'] in ('cover', 'page'):
                doc = cover if record['kind'] == 'cover' else inner
                record['image'], record['raster'] = raster(doc[record['pdf_page'] - 1], edge)
                record['source'] = 'cover_pdf' if record['kind'] == 'cover' else 'design_pdf'
            if record['kind'] == 'page':
                p = layout['pages'][record['pdf_page'] - 1]
                record['title'] = p.get('title', '')
        ratio = rect.height / rect.width
    chapters = chapter_links(snaps['story']['data']['chapters'], layout['pages'])
    count = len({i['photo_id'] for p in layout['pages'] for i in p['items']})
    book = dict(title=run.config['title'], version=version, basedOn=based_on,
                pageCount=len(layout['pages']), photoCount=count, ratio=ratio, chapters=chapters, pages=records)
    template = (ASSETS / 'index.html').read_text(encoding='utf-8')
    substitutions = {'TITLE': html.escape(book['title']), 'STYLE': (ASSETS / 'reader.css').read_text(encoding='utf-8'),
                     'BOOK': safe_json(book), 'VENDOR': (vendor / 'page-flip.browser.js').read_text(encoding='utf-8'),
                     'SCRIPT': (ASSETS / 'reader.js').read_text(encoding='utf-8'),
                     'LICENSE': (vendor / 'LICENSE.page-flip').read_text(encoding='utf-8')}
    for key, value in substitutions.items():
        template = template.replace(f'<!--{key}-->', value)
    manifest = dict(version=version, based_on_version=based_on, exported_at=now(), run_id=run.info['run_id'],
                    revisions={s: snap['revision'] for s, snap in snaps.items()},
                    source_pdfs={k: dict(sha256=file_hash(v), bytes=v.stat().st_size) for k, v in pdfs.items()},
                    reader_layout='compact-v1',original_sources_verified=True, content_pages=len(records)-3, inner_pages=len(layout['pages']),
                    photo_count=count, ratio=ratio, raster_long_edge=edge, chapters=chapters,
                    surfaces=[{k: v for k, v in r.items() if k != 'image'} for r in records],
                    synthetic_surfaces='封面内侧、封底内侧、封底：仅供网页翻阅，不属于 PDF 内页',
                    library=provenance, implementation={p.name: file_hash(p) if p.is_file() else implementation_hashes()[p.name] for p in [Path(__file__), ASSETS/'index.html', ASSETS/'reader.css', ASSETS/'reader.js']},
                    files={})
    return template, manifest


def export_web(run, destination, *, version, based_on=None, edge=1600):
    vendor = ASSETS / 'vendor'
    destination = Path(destination).expanduser().resolve()
    require(not destination.exists(), '输出版本目录已存在；请指定新版本，禁止覆盖')
    for protected in (run.source.resolve(), run.root.resolve()):
        require(destination != protected and protected not in destination.parents and destination not in protected.parents,
                '网页版本目录必须位于原图和运行目录之外')
    template, manifest = build_reader(run, version=version, based_on=based_on, edge=edge)
    book = dict(title=run.config['title'], pageCount=manifest['inner_pages'])
    count = manifest['photo_count']
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.web-export-', dir=destination.parent))
    try:
        dist = temp / 'dist'; dist.mkdir()
        (dist / 'index.html').write_text(template, encoding='utf-8')
        shutil.copyfile(vendor / 'LICENSE.page-flip', dist / 'LICENSE.page-flip.txt')
        manifest['files'] = {p.relative_to(temp).as_posix(): dict(sha256=file_hash(p), bytes=p.stat().st_size) for p in dist.iterdir()}
        write(temp / '版本清单.json', manifest)
        (temp/'使用说明.md').write_text(f'''# {book['title']} · {version} 网页预览

双击 `dist/index.html`，使用 Chrome、Edge 或 Safari 打开。所有照片页面与翻页组件都在这一个 HTML 文件里，阅读不需要网络、账号或服务器；不要用聊天软件的文本预览打开。

顶部保留书名和“章节目录、放大、全屏”三个按钮，底部只显示翻页和页码。书本按窗口可用空间等比放大，不设固定最大尺寸；窄屏自动改为单页。可拖动书角、点击页面两侧或使用左右方向键翻页，也可在章节目录中跳转章节或输入页码。点击“放大”查看当前页，电脑跨页可选择左页或右页。支持全屏和触屏滑动。系统选择“减少动态效果”时使用直接翻页。

内页 1 在右侧，其后按 2–3、4–5 配对。手机翻页跳过封面内侧空白。网页封底是阅读辅助页面，不计入 {book['pageCount']} 页内页。

本版本基于 {based_on or '当前已审阅版本'} 的最终封面和内页 PDF 逐页生成；保留 {count} 张照片、{book['pageCount']} 页内页和地点文字。未重新选片、排版或改写。网页为屏幕阅读图像，打印请继续使用原版本设计 PDF。

分享或上传此 HTML 会连同书内全部可见照片一起分享。目前只生成本地文件，没有发布到互联网。zip 是便携副本，解压后打开 `dist/index.html`。

`版本清单.json` 记录 PDF 哈希、流程修订号、逐页映射、页面图片哈希和组件来源。封面内侧、封底内侧与封底为网页辅助页面。翻页组件 StPageFlip / page-flip 2.0.7，MIT 许可证随包附带。
''', encoding='utf-8')
        with zipfile.ZipFile(temp/'照片书网页预览.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(temp.rglob('*')):
                if path.is_file() and path.suffix != '.zip':
                    archive.write(path, path.relative_to(temp))
        # Recheck snapshots immediately before publishing the independent export.
        require(all(run.snapshot(s)['revision'] == revision for s, revision in manifest['revisions'].items()), '导出期间流程结果发生变化，请重试')
        temp.rename(destination)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return destination / 'dist' / 'index.html'
