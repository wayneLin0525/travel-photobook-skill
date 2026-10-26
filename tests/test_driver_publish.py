"""Mechanical protocol tests; synthetic replies are never user photo curation."""
from copy import deepcopy
from pathlib import Path
import json
import re

import pymupdf as fitz
import pytest

from photobook.core import Run, WorkflowError, file_hash, read, write
from photobook.driver import advance, start, submit_review, revise
from photobook.publish import deliver
from tests.fixtures import make_photos, config, auto_answer
from tests.test_web_export import ReviewedRun


def test_publish_only_two_files_and_same_pages(tmp_path):
    run = ReviewedRun(tmp_path)
    output = tmp_path / '成品/V1'
    result = deliver(run, output, edge=800)
    assert {p.name for p in output.iterdir()} == {'照片书.pdf', '照片书.html'}
    with fitz.open(result['pdf']) as doc:
        assert len(doc) == 5
        assert 'cover.pdf' in doc[0].get_text()
        assert 'inner.pdf 4' in doc[4].get_text()
    text = Path(result['html']).read_text(encoding='utf-8')
    book = json.loads(re.search(r'<script type="application/json" id="book-data">(.*?)</script>', text, re.S).group(1))
    assert book['pageCount'] == 4
    assert 'Permission is hereby granted' in text
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', text)
    assert len(list((run.root / 'deliveries').glob('*.json'))) == 1
    with pytest.raises(WorkflowError, match='不能覆盖'):
        deliver(run, output)


def test_no_partial_output_on_export_failure(tmp_path, monkeypatch):
    run = ReviewedRun(tmp_path)
    def fail(*args, **kwargs):
        raise WorkflowError('组件失败')
    monkeypatch.setattr('photobook.publish.build_reader', fail)
    with pytest.raises(WorkflowError):
        deliver(run, tmp_path / 'V1')
    assert not (tmp_path / 'V1').exists()


def test_publish_rejects_fixture_mode(tmp_path):
    run = ReviewedRun(tmp_path); run.config['test_mode'] = True
    with pytest.raises(WorkflowError, match='合成测试'):
        deliver(run, tmp_path / 'V1')


def test_publisher_checks_reviewed_artifact_hash(tmp_path):
    run = ReviewedRun(tmp_path)
    run.data['render']['file_hashes'] = {'inner.pdf': file_hash(run.root / 'inner.pdf')}
    with (run.root / 'inner.pdf').open('ab') as handle:
        handle.write(b'changed')
    with pytest.raises(WorkflowError, match='发生变化'):
        deliver(run, tmp_path / 'V1')


def test_recover_publish_after_atomic_rename(tmp_path):
    from photobook.driver import _recover_delivery
    run=ReviewedRun(tmp_path);output=tmp_path/'V1';files=deliver(run,output,edge=800)
    record=next((run.root/'deliveries').glob('*.json'))
    payload=read(record);payload['status']='prepared';write(record,payload)
    job=dict(output=str(output),delivery=None)
    _recover_delivery(run,job)
    assert job['delivery']['files']==files
    assert read(record)['status']=='published'
    assert read(run.root/'job.json')['delivery']['recovered_at']


def test_recovery_refuses_changed_result(tmp_path):
    from photobook.driver import _recover_delivery
    run=ReviewedRun(tmp_path);output=tmp_path/'V1';deliver(run,output,edge=800)
    (output/'照片书.html').write_text('changed')
    with pytest.raises(WorkflowError,match='哈希'):
        _recover_delivery(run,dict(output=str(output),delivery=None))


def job_fixture(tmp_path):
    source = make_photos(tmp_path / '照片 空格', 400)
    result = start(source, output=tmp_path / '结果/V1', work_root=tmp_path / 'private')
    run = Run(result['job'])
    # Explicit fixture-only setup after production start. Production start rejects this flag.
    write(run.root / 'config.json', dict(run.config, **config()))
    return Run(run.root)


def test_resume_pending_task_and_reject_wrong_or_fake_review(tmp_path):
    run = job_fixture(tmp_path)
    assert advance(run.root)['stage'] == 'scan'
    pending = advance(run.root)
    assert pending['state'] == 'NEEDS_VISION'
    assert advance(run.root)['task'] == pending['task']
    task = read(pending['task']); answer = auto_answer(run, task, {})
    answer['seen_artifacts'] = []
    path = run.root / 'bad.json'; write(path, answer)
    with pytest.raises(WorkflowError, match='联系表'):
        submit_review(run.root, path)
    assert not (Path(pending['task']).parent / 'responses/screen-0001.json').exists()
    write(path, auto_answer(run, task, {})); submit_review(run.root, path)
    assert advance(run.root)['stage'] == 'screen'
    assert Run(run.root).status()['screen'] == 'complete'
    assert len(list(Path(pending['task']).parent.glob('attempts/*.json'))) == 3
    revise(run.root, 'screen')
    new_task = advance(run.root)
    assert new_task['task'] != pending['task']
    assert (run.root / 'head-history').is_dir()


def test_source_change_blocks_resume(tmp_path):
    run = job_fixture(tmp_path); advance(run.root)
    (run.source / 'new.txt').write_text('change')
    with pytest.raises(WorkflowError, match='原图目录已变化'):
        advance(run.root)


def test_retries_are_bounded_and_saved(tmp_path):
    run = job_fixture(tmp_path); advance(run.root); task = read(advance(run.root)['task'])
    answer = auto_answer(run, task, {}); answer['seen_artifacts'] = []
    path = run.root / 'bad.json'; write(path, answer)
    for i in range(3):
        with pytest.raises(WorkflowError):
            submit_review(run.root, path)
    with pytest.raises(WorkflowError, match='三次'):
        advance(run.root)


def test_start_cannot_enable_test_mode_or_contaminate_source(tmp_path):
    source = make_photos(tmp_path / 'photos', 200)
    with pytest.raises(WorkflowError, match='测试夹具'):
        start(source, output=tmp_path / 'out', config={'test_mode': True})
    with pytest.raises(WorkflowError, match='原图'):
        start(source, output=source / 'V1')


def test_fast_verification_detects_change_with_preserved_mtime(tmp_path):
    import os
    run=job_fixture(tmp_path);advance(run.root);run.verify_sources(full=False)
    path=run.source/'01-测试.jpg';before=path.stat()
    data=bytearray(path.read_bytes());data[-3]^=1;path.write_bytes(data)
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(WorkflowError,match='原图目录已变化'):
        run.verify_sources(full=False)


def test_cluster_batches_can_continue_one_event(tmp_path):
    import numpy as np
    from PIL import Image
    from photobook.images import scan
    from photobook.review import prepare,submit,finish
    from tests.fixtures import complete,envelope
    source=tmp_path/'photos';source.mkdir()
    for i in range(60):Image.new('RGB',(100,100),(i,70,110)).save(source/f'{i:03}.png')
    run=Run.create(source,tmp_path/'run',config());scan(run);complete(run,'screen')
    ids=run.snapshot('screen')['data']['active'];path=run.root/'vectors.npz'
    np.savez(path,ids=np.array(ids),vectors=np.eye(60,dtype='float32'))
    run.commit('embed',dict(matrix='vectors.npz'))
    complete(run,'dedup');folder=prepare(run,'cluster');batch=read(folder/'batch.json')
    assert len(batch['tasks'])==2
    for i,task in enumerate(batch['tasks']):
        answer=envelope(task);event=dict(event_id='E',title='同一活动',reason='合成连续事件，用于验证跨批归并。',photo_ids=task['target_ids'])
        if i:event['continuation_of']='cluster-0001-E'
        answer['events']=[event];path=folder/f'a{i}.json';write(path,answer);submit(run,folder,path)
    from photobook.review import extend_context_review
    task=extend_context_review(run,folder)['tasks'][-1]
    answer=auto_answer(run,task,{})
    path=folder/'global.json';write(path,answer);submit(run,folder,path)
    result=finish(run,folder)
    assert len(result['data']['events'])==1
    assert len(result['data']['events'][0]['photo_ids'])==60
