from copy import deepcopy
import json
from pathlib import Path
import pytest
from PIL import Image
from photobook.core import Run,WorkflowError,read,write,file_hash
from photobook.images import scan,open_photo
from photobook.review import prepare,submit,finish
from photobook.report import report
from photobook.layout import render,validate_plan
from tests.fixtures import make_photos,config,fixture_embed,complete,envelope,row,auto_answer

@pytest.fixture
def scanned(tmp_path):
    source=make_photos(tmp_path/'photos',500);run=Run.create(source,tmp_path/'run',config());scan(run);return run

@pytest.fixture
def embedded(scanned):
    complete(scanned,'screen');fixture_embed(scanned);return scanned

@pytest.fixture
def selected(embedded):
    for stage in ['dedup','cluster','event_select','select','story']:complete(embedded,stage)
    return embedded

def test_scan_orientation_heic_errors_and_conservation(scanned):
    data=scanned.snapshot('scan')['data'];p={v['path']:v for v in data['photos']}
    assert sum(data['counts'].values())==12
    assert p['08-测试.jpg']['height']>p['08-测试.jpg']['width']
    assert 'preview' in p['09-测试.heic']
    assert p['11-corrupt.jpg']['state']=='exclude'
    assert p['12-unsupported.txt']['state']=='exclude'
    assert all(x['state']!='exclude' for x in data['photos'] if x.get('risks'))
    assert scanned.verify_sources()

def test_initial_scan_immutable_cache(scanned):
    old=scanned.heads()['scan'];before={p['path']:p['sha256'] for p in scanned.photos().values()}
    scan(scanned)
    assert old!=scanned.heads()['scan']
    assert (scanned.root/'stages/scan'/old/'snapshot.json').exists()
    assert before=={p['path']:p['sha256'] for p in scanned.photos().values()}

def test_no_output_inside_source(tmp_path):
    src=make_photos(tmp_path/'p',100)
    with pytest.raises(WorkflowError):Run.create(src,src/'output')

def test_required_photo_decode_failure(tmp_path):
    src=make_photos(tmp_path/'p',100);r=Run.create(src,tmp_path/'run',dict(config(),must_keep=['11-corrupt.jpg']))
    with pytest.raises(WorkflowError,match='无法处理'):scan(r)

def test_missing_review_is_not_completed(scanned):
    folder=prepare(scanned,'screen')
    with pytest.raises(WorkflowError,match='未完成'):finish(scanned,folder)
    assert 'screen' not in scanned.heads()

def test_answer_missing_photo_rejected(scanned):
    folder=prepare(scanned,'screen');task=read(folder/'batch.json')['tasks'][0];a=envelope(task)
    a['decisions']=[row(pid,'keep') for pid in task['target_ids'][:-1]];write(folder/'bad.json',a)
    with pytest.raises(WorkflowError,match='每张'):submit(scanned,folder,folder/'bad.json')

def test_review_without_sheets_rejected(scanned):
    folder=prepare(scanned,'screen');task=read(folder/'batch.json')['tasks'][0];a=auto_answer(scanned,task,{})
    a['seen_artifacts']=[];write(folder/'bad.json',a)
    with pytest.raises(WorkflowError,match='联系表'):submit(scanned,folder,folder/'bad.json')

def test_source_change_invalidates(scanned):
    (scanned.source/'new.txt').write_text('new')
    with pytest.raises(WorkflowError,match='已变化'):prepare(scanned,'screen')

def test_config_change_invalidates(scanned):
    complete(scanned,'screen');scanned.config['context']='changed'
    assert scanned.status()['screen']=='stale'

def test_rescan_invalidates_downstream(embedded):
    scan(embedded)
    assert embedded.status()['screen']=='stale'
    assert embedded.status()['embed']=='stale'

def test_exact_duplicate_retains_representative(embedded):
    snap=complete(embedded,'dedup');rows=snap['data']['decisions']
    merged=[r for r in rows if r['reason_code']=='exact_duplicate']
    assert len(merged)==1
    assert merged[0]['related_photo_ids'][0] in snap['data']['active']
    assert len(snap['data']['active'])==9

def test_chain_merge_rejected(embedded):
    folder=prepare(embedded,'dedup');batch=read(folder/'batch.json')
    for task in batch['tasks']:
        a=auto_answer(embedded,task,batch['proposal']);ids=task['target_ids'];a['decisions'][0]=row(ids[0],'merge',[ids[1]]);a['decisions'][1]=row(ids[1],'merge',[ids[0]])
        write(folder/'bad.json',a);submit(embedded,folder,folder/'bad.json')
    with pytest.raises(WorkflowError,match='链式'):finish(embedded,folder)

def test_cluster_cannot_lose_photos(embedded):
    complete(embedded,'dedup');folder=prepare(embedded,'cluster');b=read(folder/'batch.json');task=b['tasks'][0];a=auto_answer(embedded,task,b['proposal']);a['events'][0]['photo_ids'].pop()
    write(folder/'bad.json',a);submit(embedded,folder,folder/'bad.json')
    with pytest.raises(WorkflowError,match='无遗漏'):finish(embedded,folder)

def test_crop_bounds_rejected(selected):
    folder=prepare(selected,'layout');b=read(folder/'batch.json');a=auto_answer(selected,b['tasks'][0],b['proposal']);a['pages'][0]['items'][0]['crop']=[-.1,0,1,1]
    with pytest.raises(WorkflowError,match='裁切'):validate_plan(selected,a)

def test_text_without_sources_rejected(selected):
    folder=prepare(selected,'layout');b=read(folder/'batch.json');a=auto_answer(selected,b['tasks'][0],b['proposal']);a['pages'][0]['text_sources']=[]
    with pytest.raises(WorkflowError,match='文字证据'):validate_plan(selected,a)

def test_complete_pipeline_renders_and_preserves_sources(selected):
    complete(selected,'layout');result=render(selected)['data'];report(selected)
    assert len(result['page_images'])==7
    assert len(result['placements'])==10
    assert result['warnings'] # 500px fixtures must NOT pass as print-ready.
    assert result['print_status']=='unverified'
    assert selected.verify_sources()
    assert (selected.root/result['design_pdf']).is_file()
    assert (selected.root/'report.html').is_file()
    complete(selected,'qa')
    assert selected.snapshot('qa')['data']['visual_status']=='issues'

def test_report_escapes_script(scanned):
    scanned.config['title']='</script><script>alert(1)</script>'
    # Read-only report displays an intentionally stale run safely.
    scanned.config=read(scanned.root/'config.json')
    snap=scanned.snapshot('scan');snap['data']['photos'][0]['path']='</script><script>alert(1)</script>?a=1&b=2'
    path=scanned.root/'stages/scan'/snap['revision']/'snapshot.json';write(path,snap)
    report(scanned);text=(scanned.root/'report.html').read_text()
    assert '</script><script>alert(1)</script>' not in text
    import re
    payload=json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',text,re.S).group(1))
    assert payload['photos'][0]['path']=='</script><script>alert(1)</script>?a=1&b=2' 

def test_diff_and_supersedes(scanned):
    first=complete(scanned,'screen')['revision'];second=complete(scanned,'screen')['revision']
    assert first!=second
    assert all(d['supersedes'] for d in scanned.snapshot('screen')['decisions'])
    assert scanned.diff('screen',first,second)['changes']==[]


def test_title_change_only_invalidates_layout_onwards(selected):
    complete(selected,'layout')
    selected.config['title']='新标题'
    assert selected.status()['scan']=='complete'
    assert selected.status()['story']=='complete'
    assert selected.status()['layout']=='stale'

def test_restore_prior_decisions(scanned):
    first=complete(scanned,'screen')['revision']
    second=complete(scanned,'screen')['revision']
    scanned.restore('screen',first)
    assert scanned.heads()['screen']==first
    assert (scanned.root/'stages/screen'/second/'snapshot.json').exists()
    assert scanned.status()['screen']=='complete'

def test_last_spread_does_not_include_cover(selected):
    complete(selected,'layout');data=render(selected)['data']
    with Image.open(selected.root/data['spreads'][-1]) as im:
        assert im.getpixel((im.width-10,im.height//2))==(230,227,220)

def test_fixture_replies_rejected_in_production(scanned):
    folder=prepare(scanned,'screen');task=read(folder/'batch.json')['tasks'][0]
    a=auto_answer(scanned,task,{})
    task['constraints']['test_mode']=False
    from photobook.review import check_envelope
    with pytest.raises(WorkflowError,match='审阅者'):check_envelope(task,a)

def test_unknown_photo_cannot_be_selected(selected):
    folder=prepare(selected,'layout');b=read(folder/'batch.json');a=auto_answer(selected,b['tasks'][0],b['proposal'])
    a['pages'][0]['items'][0]['photo_id']='P_unknown'
    with pytest.raises(WorkflowError,match='未入选'):validate_plan(selected,a)

def test_four_photo_composition_actual_render(selected):
    folder=prepare(selected,'layout');b=read(folder/'batch.json');task=b['tasks'][0];a=auto_answer(selected,task,b['proposal'])
    a['pages'][4]['items'].append(deepcopy(a['pages'][0]['items'][0]))
    a['pages'][4]['items'][-1]['role']='detail'
    a['pages'][4]['items'][0]['crop']=[0,0,1,.66]
    a['pages'][4]['items'][0]['reason']='合成素材显式裁切为宽幅主图，用于验证四图主辅布局。'
    a['reuse_reason']='测试四图主辅排版，因此在此复用已经出现的测试照片。'
    write(folder/'answer.json',a);submit(selected,folder,folder/'answer.json');finish(selected,folder)
    data=render(selected)['data']
    assert sum(p['page']==5 for p in data['placements'])==4
    reused=a['pages'][0]['items'][0]['photo_id']
    rows=[d for d in selected.snapshot('layout')['decisions'] if d['photo_id']==reused]
    assert len(rows)==2 and rows[0]['evidence']['actual_rect_mm']!=rows[1]['evidence']['actual_rect_mm']

def test_global_review_task_includes_candidates_and_constraints(selected):
    folder=prepare(selected,'story');task=read(folder/'batch.json')['tasks'][0]
    assert task['context']['selected']==selected.snapshot('select')['data']['selected']
    assert task['context']['events']

def test_restore_initial_scan(scanned):
    rev=scanned.heads()['scan'];scan(scanned);scanned.restore('scan',rev)
    assert scanned.heads()['scan']==rev

def test_short_scene_change_is_not_automatically_new_event(embedded):
    complete(embedded,'dedup')
    from photobook.review import cluster_proposal
    result=cluster_proposal(embedded)
    # Fixture frames within four minutes have orthogonal test vectors, yet belong
    # to the same continuous activity. Day changes still create boundaries.
    assert len(result['events'])==3

def test_print_embedding_uses_target_resolution_without_upscaling():
    from photobook.layout import print_image
    original=Image.new('RGB',(4000,3000),'white')
    image,ppi=print_image(original,(0,0,100,75),300)
    assert image.width<1400 and ppi>=299.5
    assert original.size==(4000,3000)
    small=Image.new('RGB',(200,150),'white')
    image,ppi=print_image(small,(0,0,100,75),300)
    assert image.size==small.size and ppi<100
