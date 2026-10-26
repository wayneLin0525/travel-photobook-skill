"""New visual handoffs must expose omissions and survive interruption."""
from copy import deepcopy
import pytest
from photobook.core import Run,read,write,WorkflowError
from photobook.images import scan
from photobook.review import prepare,submit,finish,extend_context_review
from tests.fixtures import make_photos,config,complete,fixture_embed,envelope,row,auto_answer


def ready(tmp_path):
    run=Run.create(make_photos(tmp_path/'photos',200),tmp_path/'run',config())
    scan(run);complete(run,'screen');fixture_embed(run)
    for stage in ('dedup','cluster','event_select','select'):complete(run,stage)
    return run


def test_all_omissions_are_visible_and_recovery_requires_second_look(tmp_path):
    run=ready(tmp_path)
    selected=run.snapshot('select')['data']['selected']
    exact=next(iter(run.snapshot('dedup')['data']['exact']))
    folder=prepare(run,'story');batch=read(folder/'batch.json')
    omitted=set(run.snapshot('screen')['data']['active'])-set(selected)
    assert {p for t in batch['tasks'] for p in t['target_ids']}==omitted
    assert exact in omitted
    assert all(t['contact_sheets'] and set(t['target_ids'])<=set(t['photo_ids']) for t in batch['tasks'])
    for task in batch['tasks']:
        answer=envelope(task);answer['decisions']=[row(p,'reconsider') for p in task['target_ids']]
        write(folder/'reply.json',answer);submit(run,folder,folder/'reply.json')
    with pytest.raises(WorkflowError,match='未完成'):finish(run,folder)
    expanded=extend_context_review(run,folder)
    assert expanded==extend_context_review(run,folder)  # Resuming does not append duplicates.
    task=expanded['tasks'][-1]
    assert exact in task['photo_ids'] and task['context']['reconsider_ids']==list(omitted)
    answer=deepcopy(auto_answer(run,task,{}))
    answer['coverage']=deepcopy(answer['coverage'])
    answer['selected'].append(exact);answer['chapters'][-1]['photo_ids'].append(exact)
    answer['changes']=[dict(photo_id=exact,reason='合成恢复测试，验证重新看图后可以补回去重误并图。')]
    rep=run.snapshot('dedup')['data']['exact'][exact]
    event=next(e for e in run.snapshot('cluster')['data']['events'] if rep in e['photo_ids'])
    next(c for c in answer['coverage'] if c['event_id']==event['event_id'])['selected_ids'].append(exact)
    write(folder/'assembly.json',answer);submit(run,folder,folder/'assembly.json')
    result=finish(run,folder)
    assert exact in result['data']['selected']
    assert result['data']['omission_reviews'][0]['review_task']


def test_cluster_global_review_cannot_omit_event(tmp_path):
    run=ready(tmp_path);folder=prepare(run,'cluster');batch=read(folder/'batch.json')
    for task in batch['tasks']:
        answer=auto_answer(run,task,{})
        write(folder/'local.json',answer);submit(run,folder,folder/'local.json')
    with pytest.raises(WorkflowError,match='未完成'):finish(run,folder)
    task=extend_context_review(run,folder)['tasks'][-1]
    answer=auto_answer(run,task,{})
    answer['event_groups'].pop()
    write(folder/'bad.json',answer)
    with pytest.raises(WorkflowError,match='全部已审事件'):submit(run,folder,folder/'bad.json')
