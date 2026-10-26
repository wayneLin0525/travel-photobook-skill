from copy import deepcopy
import pytest
from photobook.core import WorkflowError,write
from photobook.locations import import_locations,validate_claims
from photobook.review import prepare
from tests.test_workflow import scanned,embedded,selected

def payload(run):
    return dict(reviewer=dict(kind='test_fixture',name='location fixture',session='pytest'),sources=[],records=[
        dict(photo_id=p,city=None,city_status='unknown',place=None,place_status='unknown',confidence='low',
             kind='unknown',visited=False,label='地点未确认',uncertainty='测试照片没有GPS',
             evidence=dict(method='测试未定位',observed='合成几何图片',source_ids=[],related_photo_ids=[]))
        for p,v in run.photos().items() if v.get('preview')])

def enable(run,data):
    run.config['location_required']=True;write(run.root/'config.json',run.config)
    p=run.root/'locations.json';write(p,data);return import_locations(run,p)

def test_location_is_optional_for_old_runs(scanned):
    assert 'locate' not in scanned.status()

def test_all_decodable_photos_require_location_record(scanned):
    data=payload(scanned);data['records'].pop()
    with pytest.raises(WorkflowError,match='逐张覆盖'):enable(scanned,data)

def test_missing_gps_cannot_claim_confirmed_city(scanned):
    data=payload(scanned);data['records'][0].update(city='甲城',city_status='confirmed')
    data['sources']=[dict(source_id='S',title='fixture',url='https://example.org/',checked_at='2026-10-25',note='fixture only')]
    data['records'][0]['evidence']['source_ids']=['S']
    with pytest.raises(WorkflowError,match='无GPS'):enable(scanned,data)

def test_airborne_not_counted_as_visit(scanned):
    data=payload(scanned);data['records'][0].update(kind='airborne',visited=True)
    with pytest.raises(WorkflowError,match='不得当作城市到访'):enable(scanned,data)

def test_inference_requires_related_photo(scanned):
    data=payload(scanned);data['records'][0].update(city='甲城',city_status='inferred')
    with pytest.raises(WorkflowError,match='关联照片'):enable(scanned,data)

def test_uncertain_names_not_approved(scanned):
    data=payload(scanned);a,b=data['records'][:2]
    b.update(city='甲城',city_status='confirmed');b['evidence']['visible_signage']='测试专用城市招牌'
    a.update(city='甲城',city_status='inferred');a['evidence']['related_photo_ids']=[b['photo_id']]
    snap=enable(scanned,data)
    assert snap['data']['records'][0]['approved_names']==[]
    with pytest.raises(WorkflowError,match='未确认'):validate_claims(scanned,[dict(name='甲城',photo_ids=[a['photo_id']])],[a['photo_id']])

def test_location_revision_invalidates_downstream(selected):
    data=payload(selected);enable(selected,data)
    assert selected.status()['scan']=='complete' and selected.status()['story']=='stale'
    task=__import__('photobook.core',fromlist=['read']).read(prepare(selected,'cluster')/'batch.json')['tasks'][0]
    assert task['locations']['records']

def test_caption_cannot_omit_location_evidence(scanned):
    data=payload(scanned);a=data['records'][0];a.update(city='甲城',city_status='confirmed')
    a['evidence']['visible_signage']='测试城市招牌';enable(scanned,data)
    with pytest.raises(WorkflowError,match='未声明'):validate_claims(scanned,[],[a['photo_id']],'在甲城散步')
    validate_claims(scanned,[dict(name='甲城',photo_ids=[a['photo_id']])],[a['photo_id']],'在甲城散步')
