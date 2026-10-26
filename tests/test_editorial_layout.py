from copy import deepcopy
import pymupdf as fitz
import pytest

from photobook.core import Run, WorkflowError, read, write
from photobook.images import scan
from photobook.layout import boxes, fitted_image, page_frames, render, validate_frames, validate_plan
from photobook.review import prepare, submit, finish
from tests.fixtures import make_photos,config,fixture_embed,complete,auto_answer


@pytest.fixture
def planned(tmp_path):
    run=Run.create(make_photos(tmp_path/'photos',500),tmp_path/'run',config())
    scan(run);complete(run,'screen');fixture_embed(run)
    for stage in ['dedup','cluster','event_select','select','story','layout']:complete(run,stage)
    return run


def test_chapter_no_longer_shrinks_portrait(planned):
    pid=next(p for p,v in planned.photos().items() if v.get('width',0)<v.get('height',0))
    item=dict(photo_id=pid,crop=[0,0,1,1])
    page=dict(template='chapter',items=[item])
    old=fitted_image(planned,item,boxes('chapter',1,210,210)[0])[1]
    new=fitted_image(planned,item,page_frames(page,210,210,'editorial_v4')[0])[1]
    assert new[3]>=150 and new[2]*new[3]>old[2]*old[3]*3
    assert page_frames(page,210,210,None)==boxes('chapter',1,210,210)


def test_custom_frames_cannot_cover_photos_or_text():
    page=dict(template='double',items=[{},{}],frames_mm=[[14,32,100,140],[110,40,70,140]])
    with pytest.raises(WorkflowError,match='覆盖'):validate_frames(page,210,210,'editorial_v4')
    page['frames_mm']=[[14,20,80,140],[105,32,80,140]]
    with pytest.raises(WorkflowError,match='保留区'):validate_frames(page,210,210,'editorial_v4')
    page['frames_mm']=[[14,32,99,132],[121,86,75,100]]
    validate_frames(page,210,210,'editorial_v4')
    with pytest.raises(WorkflowError,match='设计系统'):validate_frames(page,210,210,None)


def test_editorial_review_persists_style_and_renders_metadata(planned):
    folder=prepare(planned,'layout');batch=read(folder/'batch.json');task=batch['tasks'][0]
    answer=auto_answer(planned,task,batch['proposal'])
    answer.update(cover_title='测试封面',cover_subtitle='简洁副标题',cover_caption='2026年5月')
    answer['pages'][0].update(location='测试地点',date='5月1日',caption='')
    write(folder/'answer.json',answer);submit(planned,folder,folder/'answer.json');snap=finish(planned,folder)
    assert snap['data']['design_system']=='editorial_v1'
    assert snap['data']['cover_title']=='测试封面'
    assert any(d['evidence'].get('location')=='测试地点' for d in snap['decisions'])
    result=render(planned)['data']
    with fitz.open(planned.root/result['design_pdf']) as doc:
        text=doc[0].get_text();assert '测试地点' in text and '5月1日' in text
    with fitz.open(planned.root/result['cover_pdf']) as doc:assert '测试封面' in doc[0].get_text()


def test_location_footer_requires_the_same_evidence_as_caption(planned):
    from types import SimpleNamespace
    def snapshot(stage):
        if stage=='locate':return dict(data=dict(records=[dict(photo_id=p,approved_names=['甲城'],city='甲城',place=None) for p in planned.photos()]))
        return planned.snapshot(stage)
    proxy=SimpleNamespace(config=dict(planned.config,location_required=True),snapshot=snapshot,photos=planned.photos)
    plan=deepcopy(planned.snapshot('layout')['data']);page=plan['pages'][0]
    page.update(location='甲城',location_claims=[])
    with pytest.raises(WorkflowError,match='未声明'):validate_plan(proxy,plan)
    page['location_claims']=[dict(name='甲城',photo_ids=page['text_sources'])]
    validate_plan(proxy,plan)
