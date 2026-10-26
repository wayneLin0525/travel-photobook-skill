from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

STAGES = ['scan', 'screen', 'embed', 'dedup', 'locate', 'cluster', 'event_select', 'select', 'story', 'layout', 'render', 'qa']
DEPS = {s: STAGES[:i] for i, s in enumerate(STAGES)}
DEPS['locate'] = ['scan']
CONFIG_FIELDS = {
    'scan': ['must_keep', 'exclude'],
    'screen': ['must_keep', 'exclude', 'context', 'important_people'],
    'embed': [],
    'dedup': ['must_keep', 'phash_distance', 'phash_neighbor_limit', 'neighbor_count', 'burst_seconds'],
    'locate': ['location_required'],
    'cluster': ['event_minutes', 'event_km', 'scene_similarity', 'scene_min_gap_seconds', 'context'],
    'event_select': ['must_keep', 'important_people', 'context'],
    'select': ['must_keep', 'important_people', 'context', 'pages', 'target_photos', 'min_photos', 'max_photos'],
    'story': ['must_keep', 'important_people', 'context', 'min_photos', 'max_photos'],
    'layout': ['title', 'pages', 'width_mm', 'height_mm', 'font'],
    'render': ['title', 'pages', 'width_mm', 'height_mm', 'font', 'target_ppi', 'print_profile'],
    'qa': ['print_profile'],
}
DEFAULTS = dict(title='旅行记忆', pages=32, target_photos=60, min_photos=50, max_photos=70,
                width_mm=210, height_mm=210, must_keep=[], exclude=[],
                important_people=[], context='', phash_distance=4, phash_neighbor_limit=12, neighbor_count=10,
                burst_seconds=60, event_minutes=30, event_km=2,
                scene_similarity=0.65, scene_min_gap_seconds=300, target_ppi=300, rule_version='0.2.0',
                font='resource:NotoSansSC-Regular',
                print_profile=None)

class WorkflowError(ValueError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf8')

def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text('utf8'))

def write(path, value):
    """Atomic JSON replace; published stage snapshots themselves are never overwritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(encoded(value)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def require(ok, message):
    if not ok:
        raise WorkflowError(message)

def environment():
    packages = {}
    for name in ['pillow', 'numpy', 'torch', 'open_clip_torch', 'opencv-python-headless', 'reportlab', 'pymupdf']:
        try: packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: packages[name] = None
    return dict(python=platform.python_version(), platform=platform.platform(), packages=packages)


def implementation_hashes():
    hashes={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')}
    if not hashes:
        from .runtime import RESOURCES
        manifest=RESOURCES/'build-provenance.json'
        require(manifest.is_file(),'打包引擎缺少源码版本清单，不能保存无实现来源的决定')
        hashes=read(manifest)['source_sha256']
    return hashes

class Run:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.info = read(self.root / 'run.json')
        self.config = read(self.root / 'config.json')
        self.source = Path(self.info['source'])

    @classmethod
    def create(cls, source, root, config=None):
        source, root = Path(source).expanduser().resolve(), Path(root).expanduser().resolve()
        require(source.is_dir(), '照片目录不存在')
        require(source != root and source not in root.parents and root not in source.parents,
                '输入与输出目录不能相同或互相包含')
        require(not root.exists() or not any(root.iterdir()), '运行目录必须为空；继续已有任务请使用原运行目录')
        c = dict(DEFAULTS); c.update(config or {})
        cls.validate_config(c)
        root.mkdir(parents=True, exist_ok=True)
        write(root / 'run.json', dict(run_id=uuid.uuid4().hex, source=str(source), created_at=now(), environment=environment()))
        write(root / 'config.json', c)
        write(root / 'heads.json', {})
        return cls(root)

    @staticmethod
    def validate_config(c):
        require(isinstance(c['pages'], int) and c['pages'] >= 2 and c['pages'] % 2 == 0, '内页必须为不小于 2 的偶数')
        require(0 < c['min_photos'] <= c['target_photos'] <= c['max_photos'], '照片数量范围不合法')
        require(c['width_mm'] >= 100 and c['height_mm'] >= 100, '当前六种模板要求页面宽高至少 100 mm')
        require(c['neighbor_count'] > 0 and c['phash_neighbor_limit'] > 0, '候选数量必须为正')
        require(not set(c['must_keep']) & set(c['exclude']), '必选与禁选冲突')
        require(len(c['must_keep']) <= c['max_photos'], '必选数量超过容量')

    @contextlib.contextmanager
    def lock(self):
        from filelock import FileLock, Timeout
        lock = FileLock(self.root / '.write-lock', timeout=0)
        try:
            with lock:
                yield
        except Timeout as error:
            raise WorkflowError('另一进程正在写入，请稍后继续；进程退出后系统会释放锁') from error

    def config_hash(self):
        return fingerprint(self.config)

    def stage_config_hash(self, stage):
        keys = CONFIG_FIELDS[stage] + ['rule_version', 'test_mode']
        return fingerprint({k: self.config.get(k) for k in keys})

    def heads(self):
        return read(self.root / 'heads.json')

    def snapshot(self, stage, fresh=True):
        h = self.heads()
        require(stage in h, f'环节尚未完成：{stage}')
        snap = read(self.root / 'stages' / stage / h[stage] / 'snapshot.json')
        if fresh:
            if self.config.get('location_required') and STAGES.index(stage)>STAGES.index('locate'):
                require('locate' in snap['dependencies'], f'{stage} 尚未使用地点确认结果，请重新审阅')
            require(snap['config_hash'] == self.stage_config_hash(stage), f'{stage} 因配置变更已过期')
            for dep, rev in snap['dependencies'].items():
                require(h.get(dep) == rev, f'{stage} 因 {dep} 变更已过期')
                ancestor = read(self.root / 'stages' / dep / rev / 'snapshot.json')
                require(ancestor['config_hash'] == self.stage_config_hash(dep), f'{stage} 因 {dep} 配置变更已过期')
        return snap

    def dependencies(self, stage):
        result = {}
        for dep in DEPS[stage]:
            if dep == 'locate' and not self.config.get('location_required'):
                continue
            self.snapshot(dep)
            result[dep] = self.heads()[dep]
        return result

    def commit(self, stage, data, decisions=None, deps=None, artifacts=None):
        require(stage in STAGES, '未知环节')
        deps = self.dependencies(stage) if deps is None else deps
        require(all(self.heads().get(k) == v for k, v in deps.items()), '输入已变化，拒绝提交旧结果')
        previous = self.heads().get(stage)
        revision = uuid.uuid4().hex
        folder = self.root / 'stages' / stage / revision
        folder.mkdir(parents=True)
        records = []
        old = {}
        if previous:
            old = {d['photo_id']: d['decision_id'] for d in self.snapshot(stage, False)['decisions']}
        for d in decisions or []:
            require(d.get('reason_text') and d.get('reason_code') and d.get('evidence') is not None, '决定缺少理由或证据')
            records.append(dict(run_id=self.info['run_id'], stage=stage, decision_id=uuid.uuid4().hex,
                                timestamp=now(), input_versions=deps, rule_version=self.config['rule_version'],
                                executor='local_program', related_photo_ids=[], event_id=None, page_id=None,
                                uncertainty='certain', supersedes=old.get(d.get('photo_id'))) | d)
        snap = dict(stage=stage, revision=revision, previous=previous, timestamp=now(),
                    config_hash=self.stage_config_hash(stage), config=self.config, dependencies=deps,
                    data=data, decisions=records, artifacts=artifacts or {}, implementation=implementation_hashes())
        write(folder / 'snapshot.json', snap)
        h = self.heads(); h[stage] = revision; write(self.root / 'heads.json', h)
        return snap

    def photos(self):
        return {p['photo_id']: p for p in self.snapshot('scan')['data']['photos']}

    def verify_sources(self, full=True):
        expected = self.snapshot('scan')['data']['photos']
        cache_path=self.root/'cache/source-verification.json'
        cached=read(cache_path) if cache_path.exists() else {}
        previous=cached.get('stats',{}) if cached.get('scan_revision')==self.heads()['scan'] else {}
        stats={}
        actual = {p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file()}
        failures = []
        for p in expected:
            path = self.source / p['path']
            if not path.is_file():
                failures.append(p['photo_id'])
                continue
            st=path.stat()
            signature=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
            # Full hashes at initial verification and render/delivery boundaries;
            # intermediate small review calls rehash any changed file identity.
            if full or previous.get(p['photo_id'])!=signature:
                if file_hash(path)!=p['sha256']:failures.append(p['photo_id'])
            stats[p['photo_id']]=signature
        added = sorted(actual - {p['path'] for p in expected})
        require(not failures and not added, f'原图目录已变化：修改/缺失 {failures}，新增 {added}。请重新 scan')
        write(cache_path,dict(scan_revision=self.heads()['scan'],stats=stats,checked_at=now(),mode='full' if full else 'changed_files'))
        return True

    def status(self):
        result = {}
        for s in STAGES:
            if s == 'locate' and not self.config.get('location_required') and s not in self.heads():
                continue
            try:
                self.snapshot(s); result[s] = 'complete'
            except WorkflowError as e:
                result[s] = 'stale' if s in self.heads() else 'pending'
        return result

    def must_ids(self):
        photos = self.photos()
        return {pid for pid, p in photos.items() if pid in self.config['must_keep'] or p['path'] in self.config['must_keep']}

    def diff(self, stage, a, b):
        sa = read(self.root / 'stages' / stage / a / 'snapshot.json')
        sb = read(self.root / 'stages' / stage / b / 'snapshot.json')
        da = {d['photo_id']: d for d in sa['decisions']}
        db = {d['photo_id']: d for d in sb['decisions']}
        changes = []
        fields = ['action', 'reason_text', 'related_photo_ids', 'event_id', 'page_id']
        for pid in sorted(da.keys() | db.keys()):
            x = {k: da.get(pid, {}).get(k) for k in fields}
            y = {k: db.get(pid, {}).get(k) for k in fields}
            if x != y: changes.append(dict(photo_id=pid, before=x, after=y))
        return dict(stage=stage, before=a, after=b, changes=changes, data_changed=sa['data'] != sb['data'])

    def restore(self, stage, revision):
        require(stage in STAGES and revision.isalnum(), '无效的阶段或版本')
        snap = read(self.root / 'stages' / stage / revision / 'snapshot.json')
        targets = dict(snap['dependencies'], **{stage: revision})
        for name, rev in targets.items():
            s = read(self.root / 'stages' / name / rev / 'snapshot.json')
            require(s['config_hash'] == self.stage_config_hash(name), '历史版本配置不同，请先恢复相应配置')
        scan_rev = targets.get('scan', revision)
        inventory = read(self.root / 'stages/scan' / scan_rev / 'snapshot.json')['data']['photos']
        actual = {p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file()}
        require(actual == {p['path'] for p in inventory}, '历史版本与当前原图目录不一致')
        require(all(file_hash(self.source / p['path']) == p['sha256'] for p in inventory), '原图已修改，不能直接恢复旧分析')
        heads = self.heads()
        write(self.root / 'head-history' / (uuid.uuid4().hex + '.json'), dict(timestamp=now(), heads=heads))
        heads.update(targets)
        write(self.root / 'heads.json', heads)
        return self.status()
