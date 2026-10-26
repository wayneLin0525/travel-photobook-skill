"""Audited geographic enrichment. GPS is evidence, not a nearest-POI decision.

The development workflow imports research/visual-review results. No public geocoder
is silently called, and uncertain names never become approved caption names.
"""
from collections import Counter
import math
from pathlib import Path
from urllib.parse import urlparse
from .core import read, write, require, file_hash

STATUSES={'confirmed','inferred','unknown'}
KINDS={'ground','rail','airborne','airport','unknown'}

def import_locations(run, path):
    run.verify_sources()
    require(run.config.get('location_required'), '先设置 location_required=true，再导入地点确认结果')
    payload=read(path); reviewer=payload.get('reviewer',{})
    require(reviewer.get('name') and reviewer.get('session'), '地点审阅缺少实际审阅者和会话')
    require(reviewer.get('kind')!='test_fixture' or run.config.get('test_mode'), '生产任务拒绝地点测试夹具')
    sources=payload.get('sources',[]); source_map={s.get('source_id'):s for s in sources}
    require(len(source_map)==len(sources) and None not in source_map, '地点来源编号重复或缺失')
    for s in sources:
        require(s.get('title') and s.get('checked_at') and s.get('note'), '地点来源缺少标题、日期或核验摘要')
        require(urlparse(s.get('url','')).scheme in ['https','http'], '地点来源必须提供可核查的网页地址')
    photos=run.photos(); expected={p for p,v in photos.items() if v.get('preview')}
    records=payload.get('records',[]); ids=[r.get('photo_id') for r in records]
    require(len(ids)==len(set(ids)) and set(ids)==expected, '地点确认必须逐张覆盖全部可解码照片，不能只填入选图')
    by_id={r['photo_id']:r for r in records}
    rows=[]; enriched=[]
    for raw in records:
        r=dict(raw); pid=r['photo_id'];p=photos[pid];m=p['metadata']
        require(r.get('city_status') in STATUSES and r.get('place_status') in STATUSES, '地点确认状态无效')
        require(r.get('confidence') in ['high','medium','low'], '地点确认必须标记可信程度')
        require(r.get('kind') in KINDS and isinstance(r.get('visited'),bool), '必须区分地面、机场和交通途中及是否到访')
        require(not r['visited'] or r['kind'] in ['ground','airport'], '机上/列车沿途坐标不得当作城市到访')
        require(r.get('label') and r.get('uncertainty') is not None, '地点缺少展示说明或不确定性')
        evidence=r.get('evidence',{})
        require(evidence.get('method') and evidence.get('observed'), '地点决定缺少实际判断方法和画面观察')
        refs=evidence.get('source_ids',[]);related=evidence.get('related_photo_ids',[])
        require(set(refs)<=source_map.keys() and set(related)<=expected, '地点证据引用未知来源或照片')
        gps=None
        if 'GPSLatitude' in m and 'GPSLongitude' in m:
            lat,lon=float(m['GPSLatitude']),float(m['GPSLongitude'])
            require(math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180, '原始GPS无效')
            gps=dict(latitude=lat,longitude=lon,coordinate_system='EXIF GPS / WGS84 assumed')
        for field in ['city','place']:
            status=r[field+'_status'];name=r.get(field)
            require((status=='unknown')==(not bool(name)), '未知地点不得填写确定名称；有名称必须声明确认或推断')
            if status=='confirmed':
                require(refs or evidence.get('visible_signage'), '已确认名称缺少外部来源或可见招牌')
                require(gps or evidence.get('visible_signage'), '无GPS的关联推断不得冒充已确认地点')
        if not gps and (r['city_status']=='inferred' or r['place_status']=='inferred'):
            require(related, '无GPS推断必须引用可回看的关联照片')
            cities={by_id[p].get('city') for p in related if by_id[p].get('city_status')=='confirmed'}
            if r['city_status']=='inferred':require(cities=={r['city']}, '关联照片城市冲突，不得推断确定城市')
        r['raw_gps']=gps; r['source_sha256']=p['sha256']
        r['approved_names']=[r[f] for f in ['city','place'] if r.get(f) and r[f+'_status']=='confirmed']
        enriched.append(r)
        rows.append(dict(photo_id=pid,action='locate',executor=reviewer,reason_code='location_'+r['place_status'],
                         reason_text=r['label']+'；'+evidence['method'],evidence=r,related_photo_ids=related,
                         uncertainty=r['uncertainty'] or '无新增不确定性'))
    artifact=run.root/'location-inputs'/f'{file_hash(path)}.json'
    if not artifact.exists():write(artifact,payload)
    data=dict(records=enriched,sources=sources,reviewer=reviewer,input_file=str(artifact.relative_to(run.root)),
              counts=dict(Counter(r['place_status'] for r in enriched)),
              city_counts=dict(Counter(r.get('city') or '交通途中或未确认' for r in enriched)),
              note='研究与视觉审阅结果导入；未接入无人值守地图API。拍摄位置与画面中远处景物分别记录，未知地点不强行补齐。')
    return run.commit('locate',data,rows)

def location_context(run, ids):
    if not run.config.get('location_required'):return None
    data=run.snapshot('locate')['data']; wanted=set(ids)
    return dict(records=[r for r in data['records'] if r['photo_id'] in wanted],sources=data['sources'],
                rule='只有approved_names可作为确定地点写入正文；inferred只可带不确定说明；visited=false不计入游览城市覆盖。')

def validate_claims(run, claims, text_sources, text=''):
    records={r['photo_id']:r for r in run.snapshot('locate')['data']['records']}
    for claim in claims:
        ids=claim.get('photo_ids',[]);name=claim.get('name')
        require(name and ids and set(ids)<=set(text_sources), '正文地点声明必须关联本段文字证据照片')
        require(all(name in records[p]['approved_names'] for p in ids), '正文地点未确认或与所引照片位置不符')
    known={r[f] for r in records.values() for f in ['city','place'] if r.get(f)}
    require({name for name in known if name in text}<={c.get('name') for c in claims}, '正文出现已知地名但未声明其照片证据')
