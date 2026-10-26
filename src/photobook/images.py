from __future__ import annotations
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps
import pillow_heif
from .core import file_hash, fingerprint, read, write, require, WorkflowError
from .runtime import exiftool_command, font_path

pillow_heif.register_heif_opener()
SUPPORTED = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}

def open_photo(path):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        profile = im.info.get('icc_profile')
        im = im.convert('RGB')
        if profile:
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                              ImageCms.createProfile('sRGB'), outputMode='RGB')
            except (ValueError, OSError) as e:
                raise WorkflowError(f'ICC 配置无法转换：{e}')
        return im.copy()

def metadata(source):
    if os.environ.get('PHOTOBOOK_METADATA', 'pillow') != 'exiftool':
        from .metadata import read_metadata
        return read_metadata(source)
    command = exiftool_command()
    env = dict(os.environ, LC_ALL='C', LC_CTYPE='C', LANG='C')
    version = subprocess.check_output(command + ['-ver'], text=True, env=env).strip()
    result = subprocess.run(command + ['-json', '-n', '-r', '-DateTimeOriginal', '-SubSecDateTimeOriginal',
                            '-OffsetTimeOriginal', '-GPSLatitude', '-GPSLongitude', '-Make', '-Model',
                            '-SerialNumber', '-Orientation', '-ICC_Profile:ProfileDescription', '-charset', 'filename=UTF8', str(source)],
                            capture_output=True, text=True, encoding='utf-8', env=env)
    require(result.returncode in (0, 1), f'ExifTool 失败：{result.stderr}')
    rows = json.loads(result.stdout or '[]')
    return {str(Path(r['SourceFile']).resolve()): r for r in rows}, version, result.stderr

def time_value(p):
    m = p.get('metadata', {})
    raw = m.get('DateTimeOriginal')
    if not raw: return None
    try:
        dt = datetime.strptime(raw[:19], '%Y:%m:%d %H:%M:%S')
        # Never assume the machine timezone; naive camera times remain explicitly naive.
        offset = m.get('OffsetTimeOriginal')
        return datetime.fromisoformat(dt.isoformat() + (offset or ''))
    except (ValueError, TypeError): return None

def delta_seconds(a, b):
    x, y = time_value(a), time_value(b)
    if x is None or y is None or (x.tzinfo is None) != (y.tzinfo is None): return None
    return abs((x-y).total_seconds())

def distance_km(a, b):
    ma, mb = a['metadata'], b['metadata']
    keys = ['GPSLatitude', 'GPSLongitude']
    if not all(k in m for k in keys for m in [ma, mb]): return None
    la, lo = [math.radians(float(ma[k])) for k in keys]
    lb, lp = [math.radians(float(mb[k])) for k in keys]
    v = math.sin((lb-la)/2)**2 + math.cos(la)*math.cos(lb)*math.sin((lp-lo)/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(v)))

def scan(run):
    require(all(not path.is_symlink() or run.source in path.resolve().parents
                for path in run.source.rglob('*') if path.is_file()),'输入链接越出照片目录')
    meta, version, warnings = metadata(run.source)
    photos, decisions = [], []
    for path in sorted(run.source.rglob('*')):
        if not path.is_file(): continue
        require(not path.is_symlink() or run.source in path.resolve().parents, f'输入链接越出照片目录：{path}')
        relative = path.relative_to(run.source).as_posix()
        digest = file_hash(path)
        pid = 'P' + fingerprint([relative, digest])[:16]
        p = dict(photo_id=pid, path=relative, sha256=digest, size=path.stat().st_size,
                 suffix=path.suffix.lower(), metadata=meta.get(str(path.resolve()), {}))
        reason, action = 'available', 'keep'
        desc = '已解码并生成预览；技术指标仅用于复核，不代表纪念价值。'
        if p['suffix'] not in SUPPORTED:
            action, reason, desc = 'exclude', 'unsupported_format', '当前版本尚未处理此格式，记录为未处理文件。'
        else:
            cache = run.root / 'cache/scan' / (digest + '-rgb-v1.json')
            try:
                if cache.exists() and (run.root / read(cache)['preview']).exists():
                    p.update(read(cache))
                else:
                    im = open_photo(path)
                    pixel_hash = hashlib.sha256(str(im.size).encode() + im.tobytes()).hexdigest()
                    small = im.copy(); small.thumbnail((1024, 1024))
                    gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
                    metrics = dict(sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                                   dark_fraction=float((gray < 10).mean()), bright_fraction=float((gray > 245).mean()))
                    flags = []
                    if max(im.size) < 1200: flags.append('low_resolution')
                    if metrics['sharpness'] < 40: flags.append('possible_blur')
                    if metrics['dark_fraction'] > .75: flags.append('mostly_dark')
                    if metrics['bright_fraction'] > .75: flags.append('mostly_bright')
                    preview = Path('previews') / (digest + '.jpg')
                    (run.root / preview).parent.mkdir(exist_ok=True)
                    small.save(run.root / preview, quality=88)
                    cached = dict(width=im.width, height=im.height, pixel_hash=pixel_hash,
                                  phash=str(imagehash.phash(im)), metrics=metrics, risks=flags,
                                  preview=str(preview), color_processing='ICC to sRGB; untagged assumed sRGB')
                    write(cache, cached); p.update(cached)
                if p['risks']:
                    action, reason = 'review', 'quality_risk'
                    desc = '疑似技术问题：' + ', '.join(p['risks']) + '；保留进入视觉复核，不自动淘汰。'
            except Exception as e:
                action, reason, desc = 'exclude', 'decode_failed', f'解码或颜色转换失败：{type(e).__name__}: {e}'
        if relative in run.config['exclude'] or pid in run.config['exclude']:
            action, reason, desc = 'exclude', 'user_excluded', '用户在配置中明确禁选。'
        p['state'] = action; photos.append(p)
        decisions.append(dict(photo_id=pid, action=action, reason_code=reason, reason_text=desc,
                              evidence=dict(path=relative, metrics=p.get('metrics'), risks=p.get('risks', []))))
    require(photos, '输入目录没有文件')
    known = {p['photo_id'] for p in photos} | {p['path'] for p in photos}
    require(set(run.config['must_keep'] + run.config['exclude']) <= known, '必选/禁选项没有匹配原图路径或 ID')
    bad_must = [p['path'] for p in photos if p['state'] == 'exclude' and
                (p['path'] in run.config['must_keep'] or p['photo_id'] in run.config['must_keep'])]
    require(not bad_must, f'必选图片无法处理：{bad_must}')
    counts = {state: sum(p['state'] == state for p in photos) for state in ['keep','review','exclude']}
    return run.commit('scan', dict(photos=photos, counts=counts, metadata_reader=version,
                                  exiftool=version if os.environ.get('PHOTOBOOK_METADATA')=='exiftool' else None,
                                  metadata_warnings=warnings), decisions)

def contact_sheets(run, ids, folder, stem='contact'):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    photos = run.photos(); paths = []
    ordinals={pid:i+1 for i,pid in enumerate(photos)}
    font = ImageFont.truetype(font_path(), 17)
    for start in range(0, len(ids), 12):
        count=min(12,len(ids)-start)
        sheet = Image.new('RGB', (min(4,count)*300, math.ceil(count/4)*330+8), '#f4f2ec'); draw = ImageDraw.Draw(sheet)
        for n, pid in enumerate(ids[start:start+12]):
            x,y = n%4*300, n//4*330
            p = photos[pid]
            if p.get('preview'):
                with Image.open(run.root / p['preview']) as im:
                    im.thumbnail((284, 280)); sheet.paste(im, (x+(300-im.width)//2, y+(284-im.height)//2))
            draw.text((x+8,y+290), pid, fill='#242c31', font=font)
            draw.text((x+8,y+313), f'#{ordinals[pid]:03d} '+Path(p['path']).stem[-22:], fill='#677078', font=font)
        name = folder / f'{stem}-{start//12:03d}.jpg'; sheet.save(name, quality=90); paths.append(str(name.relative_to(run.root)))
    return paths
