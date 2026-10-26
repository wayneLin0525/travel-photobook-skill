"""Durable host-driven workflow: real image review is never simulated by Python."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

from .core import Run, STAGES, WorkflowError, file_hash, now, read, require, write
from .runtime import RESOURCES, cache_root, exiftool_command, font_path, platform_tag


def doctor(source=None):
    checks = []
    for package in ('PIL', 'pillow_heif', 'numpy', 'cv2', 'reportlab', 'pymupdf', 'open_clip', 'torch'):
        checks.append(dict(name=package, ok=importlib.util.find_spec(package) is not None))
    checks.append(dict(name='font', ok=Path(font_path()).is_file(), path=font_path()))
    from .metadata import READER_VERSION
    checks.append(dict(name='metadata_reader', ok=True, version=READER_VERSION))
    if source:
        path = Path(source).expanduser().resolve()
        checks.append(dict(name='source', ok=path.is_dir() and os.access(path, os.R_OK), path=str(path)))
    spec = read(RESOURCES / 'assets.json')['model']
    weights = cache_root() / 'models' / spec['id'] / spec['filename']
    # Existence is informational; model_asset verifies the hash before loading.
    return dict(state='READY' if all(c['ok'] for c in checks) else 'ENVIRONMENT_MISSING',
                platform=platform_tag(), checks=checks, model=dict(id=spec['id'], cached=weights.is_file()),
                host_requirements=['read_local_photos', 'execute_program', 'view_images', 'present_files'],
                host_vision_verified=False)


def start(source, *, output, work_root=None, config=None):
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    options = dict(config or {})
    require(not options.get('test_mode'), '正式入口不接受测试夹具模式')
    require(not output.exists(), '结果版本已存在，请指定新版本')
    options.setdefault('location_required', True)
    root = Path(work_root) if work_root else cache_root() / 'jobs'
    root = root.expanduser().resolve() / uuid.uuid4().hex
    for protected in (source, root):
        require(output != protected and protected not in output.parents and output not in protected.parents,
                '结果目录不能与原图或内部目录互相包含')
    run = Run.create(source, root, options)
    write(root / 'job.json', dict(schema_version=1, created_at=now(), output=str(output),
                                 auto_pages='pages' not in options,
                                 auto_photos=not any(k in options for k in ('min_photos','target_photos','max_photos')),
                                 pending=None, delivery=None, failures=[]))
    return dict(state='STARTED', job=str(root), output=str(output), next='advance')


def _fresh_batch(run, stage, job):
    pending = job.get('pending')
    if pending and pending['stage'] == stage:
        folder = run.root / pending['folder']
        batch = read(folder / 'batch.json')
        if batch['dependencies'] == run.dependencies(stage) and batch['config_hash'] == run.config_hash():
            return folder, batch
    if stage == 'locate':
        folder, batch = _prepare_locations(run)
    else:
        from .review import prepare
        folder = prepare(run, stage); batch = read(folder / 'batch.json')
    job['pending'] = dict(stage=stage, folder=str(folder.relative_to(run.root)), attempts={})
    write(run.root / 'job.json', job)
    return folder, batch


def _prepare_locations(run):
    from .images import contact_sheets
    ids = [pid for pid, p in run.photos().items() if p.get('preview')]
    folder = run.root / 'reviews/locate' / uuid.uuid4().hex
    folder.mkdir(parents=True); tasks = []; photos = run.photos()
    for start_at in range(0, len(ids), 24):
        group = ids[start_at:start_at + 24]; tid = f'locate-{len(tasks)+1:04}'
        sheets = contact_sheets(run, group, folder / tid)
        task = dict(task_id=tid, stage='locate', photo_ids=group, target_ids=group,
                    contact_sheets=sheets, page_images=[], constraints=run.config,
                    prompt='核实城市与具体地点，区分游览与交通途中。逐图记录；无证据就unknown，不能猜地名。外部来源需实际打开核查。',
                    photos=[dict(photo_id=p, original=str(run.source / photos[p]['path']),
                                 metadata=photos[p]['metadata']) for p in group])
        write(folder / f'{tid}.json', task); tasks.append(task)
    batch = dict(batch_id=folder.name, stage='locate', dependencies=run.dependencies('locate'),
                 config_hash=run.config_hash(), tasks=tasks)
    write(folder / 'batch.json', batch)
    return folder, batch


def _finish_locations(run, folder, batch):
    records = []; sources = {}; reviewers = []
    for task in batch['tasks']:
        answer = read(folder / 'responses' / f'{task["task_id"]}.json')
        records.extend(answer['records']); reviewers.append(answer['reviewer'])
        for source in answer.get('sources', []):
            key = source.get('source_id')
            require(key not in sources or sources[key] == source, '地点来源ID有冲突，请在不同任务使用唯一ID')
            sources[key] = source
    payload = dict(reviewer=dict(kind='visual_model', name='; '.join(sorted({r['name'] for r in reviewers})),
                                 session='; '.join(sorted({r['session'] for r in reviewers}))),
                   sources=list(sources.values()), records=records)
    path = folder / 'merged-locations.json'; write(path, payload)
    from .locations import import_locations
    return import_locations(run, path)


def _task_result(run, folder, task, *, error=None):
    pending = read(run.root / 'job.json')['pending']
    images = task['contact_sheets'] + task['page_images']
    earlier=[]
    if task['stage']=='cluster':
        for path in sorted((folder/'responses').glob('*.json')):
            answer=read(path)
            earlier.extend(dict(event_id=answer['task_id']+'-'+e['event_id'],title=e['title'],
                                boundary_photo_ids=e['photo_ids'][-4:]) for e in answer.get('events',[]))
    return dict(state='NEEDS_LOCATION' if task['stage'] == 'locate' else 'NEEDS_VISION',
                job=str(run.root), stage=task['stage'], task=str(folder / f'{task["task_id"]}.json'),
                task_id=task['task_id'], images=[str(run.root / p) for p in images],
                attempts=pending['attempts'].get(task['task_id'], 0), error=error,previous_cluster_events=earlier,
                instruction='助手实际查看列出的图片并提交结构化结果，然后继续advance；不是要求用户填写。')


def _review(run, stage, job):
    folder, batch = _fresh_batch(run, stage, job)
    if stage in ('cluster','story'):
        from .review import extend_context_review
        batch=extend_context_review(run,folder)
    for task in batch['tasks']:
        if not (folder / 'responses' / f'{task["task_id"]}.json').exists():
            require(job['pending']['attempts'].get(task['task_id'], 0) < 3,
                    '同一任务已修正三次仍未通过，保留记录后停止：' + task['task_id'])
            return _task_result(run, folder, task)
    try:
        if stage == 'locate':
            _finish_locations(run, folder, batch)
        else:
            from .review import finish
            finish(run, folder)
    except WorkflowError as error:
        # Keep all rejected replies. Reopen this batch instead of silently accepting or losing decisions.
        rejected = folder / 'rejected' / uuid.uuid4().hex
        rejected.mkdir(parents=True)
        for response in (folder / 'responses').glob('*.json'):
            response.rename(rejected / response.name)
        write(rejected / 'error.json', dict(error=str(error), at=now()))
        return _task_result(run, folder, batch['tasks'][0], error=str(error))
    job['pending'] = None; write(run.root / 'job.json', job)
    if stage in ('screen','dedup'):
        _trim_capacity(run,job,len(run.snapshot(stage)['data']['active']))
    return dict(state='PROGRESS', stage=stage, job=str(run.root), next='advance')


def _trim_capacity(run,job,count):
    require(count>0,'没有可用照片，保留审阅记录后停止')
    config=dict(run.config)
    if job.get('auto_photos'):
        for name in ('min_photos','target_photos','max_photos'):
            config[name]=min(count,config[name])
    if job.get('auto_pages'):
        config['pages']=min(config['pages'],max(2,2*((count+3)//4)))
    if config!=run.config:
        write(run.root/'config-history'/f'{run.config_hash()}.json',run.config)
        write(run.root/'config.json',config);run.config=config


def advance(job_path):
    run = Run(job_path)
    with run.lock():
        job = read(run.root / 'job.json')
        if not job.get('delivery') and Path(job['output']).exists():
            _recover_delivery(run,job)
        if job.get('delivery'):
            for name, path in job['delivery']['files'].items():
                require(Path(path).is_file() and file_hash(path) == job['delivery']['sha256'][name], '已发布文件已改变或缺失')
            return dict(state='COMPLETE', **job['delivery']['files'])
        status = run.status()
        stage = next((s for s in STAGES if not (s == 'locate' and not run.config.get('location_required'))
                      and status.get(s) != 'complete'), None)
        if stage is None:
            qa = run.snapshot('qa')['data']
            if qa['visual_status'] != 'passed' or qa.get('unresolved'):
                return dict(state='NEEDS_CORRECTION', stage='qa', issues=qa, job=str(run.root),
                            instruction='按问题修正layout并重新render、实际审阅qa；不能直接清空问题。')
            return dict(state='READY_TO_DELIVER', job=str(run.root), output=job['output'])
        if stage == 'scan':
            from .images import scan
            scan(run)
            active = sum(p['state'] != 'exclude' for p in run.photos().values())
            require(active > 0, '没有可用照片')
            _trim_capacity(run,job,active)
        elif stage == 'embed':
            from .models import embed
            result = embed(run, limit=24)
            if result.get('pending'):
                return dict(state='PROGRESS', stage=stage, **result)
        elif stage == 'render':
            from .layout import render
            render(run)
        else:
            return _review(run, stage, job)
        return dict(state='PROGRESS', stage=stage, job=str(run.root), next='advance')


def submit_review(job_path, answer_path):
    run = Run(job_path)
    with run.lock():
        job = read(run.root / 'job.json'); pending = job.get('pending')
        require(pending, '当前没有待处理审阅任务')
        folder = run.root / pending['folder']; batch = read(folder / 'batch.json')
        answer = read(answer_path)
        task = next((t for t in batch['tasks'] if t['task_id'] == answer.get('task_id')), None)
        require(task, '答复不属于当前审阅任务')
        tid = task['task_id']; attempts = pending['attempts'].get(tid, 0)
        require(attempts < 3, '同一任务已修正三次，停止并保留记录')
        require(not (folder / 'responses' / f'{tid}.json').exists(), '任务已提交；使用revise重做阶段')
        pending['attempts'][tid] = attempts + 1
        write(run.root / 'job.json', job)
        write(folder / 'attempts' / f'{tid}-{attempts+1}.json', answer)
        try:
            if pending['stage'] == 'locate':
                from .review import check_envelope
                run.verify_sources(full=False)
                require(batch['config_hash'] == run.config_hash() and batch['dependencies'] == run.dependencies('locate'), '地点任务输入已过期')
                check_envelope(task, answer)
                ids = [r.get('photo_id') for r in answer.get('records', [])]
                require(len(ids) == len(set(ids)) and set(ids) == set(task['target_ids']), '地点答复必须逐张覆盖本批照片')
                write(folder / 'responses' / f'{tid}.json', answer)
            else:
                from .review import submit
                submit(run, folder, answer_path)
        except (WorkflowError, KeyError, TypeError) as error:
            write(folder / 'attempts' / f'{tid}-{attempts+1}.error.json', dict(error=str(error), at=now()))
            raise WorkflowError(str(error)) from error
        return dict(state='REVIEW_SUBMITTED', task_id=tid, next='advance')


def revise(job_path, stage):
    require(stage in ('screen', 'dedup', 'locate', 'cluster', 'event_select', 'select', 'story', 'layout', 'qa'), '只能重做审阅环节')
    run = Run(job_path)
    with run.lock():
        job = read(run.root / 'job.json')
        require(not job.get('delivery'), '已发布任务不可修改，请建立新版本任务')
        count = job.setdefault('revisions', {}).get(stage, 0)
        require(count < 3, '同一阶段已重做三轮，保留记录后停止')
        job['revisions'][stage] = count + 1
        heads = run.heads()
        write(run.root / 'head-history' / f'{uuid.uuid4().hex}.json', dict(heads=heads, stage=stage, at=now()))
        for name in STAGES[STAGES.index(stage):]:
            heads.pop(name, None)
        write(run.root / 'heads.json', heads)
        job['pending'] = None; write(run.root / 'job.json', job)
    return dict(state='REVISING', stage=stage, next='advance')


def deliver_job(job_path):
    run = Run(job_path)
    with run.lock():
        job = read(run.root / 'job.json')
        if not job.get('delivery') and Path(job['output']).exists():
            _recover_delivery(run,job)
            return dict(state='COMPLETE',**job['delivery']['files'])
        require(not job.get('delivery'), '任务已发布；直接读取status')
        from .publish import deliver
        files = deliver(run, job['output'])
        job['delivery'] = dict(files=files, sha256={k: file_hash(v) for k, v in files.items()})
        write(run.root / 'job.json', job)
        return dict(state='COMPLETE', **files)


def _recover_delivery(run,job):
    """Recover the narrow crash window after atomic rename but before job update."""
    output=Path(job['output'])
    for path in sorted((run.root/'deliveries').glob('*.json'),reverse=True):
        record=read(path)
        if record.get('destination')!=str(output):continue
        require(all(run.snapshot(s)['revision']==r for s,r in record['revisions'].items()),'现有成品与任务版本不同，不能恢复为完成')
        require({p.name for p in output.iterdir()}=={'照片书.pdf','照片书.html'},'现有成品目录不是本任务的两个文件')
        for name,info in record['final_files'].items():
            require(file_hash(output/name)==info['sha256'],'现有成品哈希与发布记录不同')
        run.verify_sources()
        files=dict(pdf=str(output/'照片书.pdf'),html=str(output/'照片书.html'))
        job['delivery']=dict(files=files,sha256={k:file_hash(v) for k,v in files.items()},recovered_at=now())
        write(run.root/'job.json',job)
        record['status']='published';write(path,record)
        return
    raise WorkflowError('输出目录已被占用且没有匹配发布记录，请为新任务选择新版本；不能覆盖')
