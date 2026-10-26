"""Reusable design checks with independent geometry fixtures, no travel-specific paths."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from photobook.core import WorkflowError,read,write
from photobook.design_policy import default_policy,resolve_answer,assess_plan
from photobook.layout import page_frames
from photobook.review import prepare,submit,finish
from tests.fixtures import auto_answer
from tests.test_editorial_layout import planned


def album(aspects):
    photos={f'photo-{i}':dict(width=round(a*1000),height=1000) for i,a in enumerate(aspects)}
    return SimpleNamespace(config=dict(width_mm=210,height_mm=210),photos=lambda:photos)


def plan(aspects,hero=0,template=None):
    return dict(design_system='editorial_v1',layout_policy=default_policy(),pages=[dict(number=1,
        template=template or ('single' if len(aspects)==1 else 'grid' if len(aspects)>2 else 'double'),
        title='沿途',caption='',location='某城',date='5月1日',
        items=[dict(photo_id=f'photo-{i}',role='hero' if i==hero else 'detail',crop=[0,0,1,1]) for i in range(len(aspects))])])


def test_new_tasks_cannot_fall_back_to_legacy_when_flag_missing(planned):
    folder=prepare(planned,'layout');b=read(folder/'batch.json');task=b['tasks'][0]
    assert task['layout_policy']['design_system']=='editorial_v1'
    answer=auto_answer(planned,task,b['proposal'])
    assert 'design_system' not in answer  # Intentional omission by a future caller.
    write(folder/'answer.json',answer);submit(planned,folder,folder/'answer.json');snap=finish(planned,folder)
    assert snap['data']['design_system']=='editorial_v1'
    assert snap['data']['layout_policy']['version']=='1.0.0'
    assert snap['data']['design_checks']['status']=='passed'
    assert all(d['evidence']['actual_rect_mm'] for d in snap['decisions'])


def test_new_task_refuses_explicit_legacy_bypass():
    with pytest.raises(WorkflowError,match='旧快照'):resolve_answer(dict(design_system='editorial_v4'),default_policy())


def test_rejected_layout_is_recorded_without_replacing_working_revision(planned):
    before=planned.heads()['layout'];folder=prepare(planned,'layout');b=read(folder/'batch.json');task=b['tasks'][0]
    answer=auto_answer(planned,task,b['proposal']);answer['pages'][0]['title']='某城｜沿途'
    write(folder/'answer.json',answer);submit(planned,folder,folder/'answer.json')
    with pytest.raises(WorkflowError,match='分隔符'):finish(planned,folder)
    assert planned.heads()['layout']==before
    errors=list((folder/'validation-errors').glob('*.json'));assert len(errors)==1
    record=read(errors[0]);assert '第1页' in record['error'] and record['policy']['version']=='1.0.0'


@pytest.mark.parametrize('aspects,hero,composition',[
    ([.75],0,'feature'),
    ([.75,.75],0,'balanced_pair'),
    ([1.33,.75],0,'hero_pair'),
    ([.75,1.5,.75],1,'wide_hero_with_details'),
    ([.75,.75,.75],2,'portrait_hero_with_details'),
    ([1.5,.75,.75,.75],0,'wide_hero_with_details'),
])
def test_composition_uses_orientation_and_roles(aspects,hero,composition):
    report=assess_plan(album(aspects),plan(aspects,hero))
    page=report['pages'][0];assert page['composition']==composition
    if len(aspects)>2:
        areas=[p['actual_rect_mm'][2]*p['actual_rect_mm'][3] for p in page['photos']]
        assert areas[hero]>max(a for i,a in enumerate(areas) if i!=hero)
    assert all(min(p['actual_rect_mm'][2:])>=36 for i,p in enumerate(page['photos']) if i!=hero)


def test_four_portraits_request_redistribution_instead_of_equal_grid():
    a=[.75]*4;p=plan(a)
    frames=page_frames(p['pages'][0],210,210,'editorial_v1',a)
    assert len({round(f[2]*f[3]) for f in frames})>1
    with pytest.raises(WorkflowError,match='尺寸过小'):assess_plan(album(a),p)


def test_actual_photo_size_checked_inside_a_nominally_large_box():
    a=[.75];p=plan(a,template='chapter');p['pages'][0]['frames_mm']=[[14,90,182,84]]
    with pytest.raises(WorkflowError,match='单图被压小'):assess_plan(album(a),p)


def test_equal_four_grid_requires_a_real_sequence_reason():
    a=[.75]*4;p=plan(a);page=p['pages'][0]
    page['frames_mm']=[[14,32,87,73],[109,32,87,73],[14,113,87,73],[109,113,87,73]]
    with pytest.raises(WorkflowError,match='缺少层级'):assess_plan(album(a),p)
    page.update(composition='equal_sequence',composition_reason='同一人物的四个连续动作，需要等重展示以比较姿态。')
    report=assess_plan(album(a),p)
    assert report['pages'][0]['composition_reason']==page['composition_reason']


def test_simplified_copy_is_enforced_but_natural_punctuation_is_allowed():
    a=[.75];p=plan(a);page=p['pages'][0]
    page['title']='某城｜海边'
    with pytest.raises(WorkflowError,match='分隔符'):assess_plan(album(a),p)
    page['title']='海边';page['caption']='5月1日 / 某城，海边散步。'
    with pytest.raises(WorkflowError,match='分隔符'):assess_plan(album(a),p)
    page['caption']='这张照片拍于离开前，身后的路随后封闭。'
    with pytest.raises(WorkflowError,match='必要背景'):assess_plan(album(a),p)
    page['caption_reason']='补充照片本身看不出的封路时间背景，说明这一刻为何特殊。'
    assert assess_plan(album(a),p)['status']=='passed'
