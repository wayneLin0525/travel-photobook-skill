"""Synthetic fixtures only. No generated decisions represent real photo curation."""
from pathlib import Path
import shutil
import numpy as np
from PIL import Image,ImageDraw,ImageFilter
import pillow_heif
from photobook.core import Run,read,write
from photobook.images import scan
from photobook.review import prepare,submit,finish,extend_context_review

pillow_heif.register_heif_opener()

def make_photos(folder, size=1000):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    colors=[('#99b9be','#294a45'),('#d1ac87','#765943'),('#91ac82','#344b33'),('#c4bd9d','#575343'),('#84a9bf','#304855'),('#c7a29d','#704a45'),('#adc3b5','#4c6555'),('#b4a3bd','#594760'),('#c9b987','#6e5e30')]
    for i,(bg,fg) in enumerate(colors):
        dims=(size,size) if i!=7 else (int(size*.75),size)
        im=Image.new('RGB',dims,bg);d=ImageDraw.Draw(im)
        d.ellipse((dims[0]*.60,80,dims[0]*.84,80+size*.24),fill='#e8d7a5')
        d.polygon([(0,size*.7),(dims[0]*.25,size*(.25+i*.025)),(dims[0]*.6,size*.65),(dims[0],size*.45),(dims[0],size),(0,size)],fill=fg)
        d.rectangle((50,size*.73,95+i*13,size*.88),fill='#e8e1cf')
        d.text((30,30),f'SYNTHETIC TEST {i+1:02}',fill='#253533',font_size=24)
        exif=im.getexif();exif[36867]=f'2026:05:{i//3+1:02} 10:{i%3*4:02}:00';exif[271]='Fixture';exif[272]='Test Camera'
        if i==7:
            im=im.transpose(Image.Transpose.ROTATE_90);exif[274]=6
        if i==8:im.save(folder/f'{i+1:02}-测试.heic',format='HEIF',exif=exif)
        else:im.save(folder/f'{i+1:02}-测试.jpg',quality=94,exif=exif)
    shutil.copy2(folder/'01-测试.jpg',folder/'10-exact-copy.jpg')
    (folder/'11-corrupt.jpg').write_bytes(b'not a jpeg')
    (folder/'12-unsupported.txt').write_text('<script>alert(1)</script>')
    return folder

def config():
    return dict(title='流程测试样书 · 合成素材',pages=6,target_photos=9,min_photos=8,max_photos=10,test_mode=True)

def fixture_embed(run):
    ids=run.snapshot('screen')['data']['active'];photos=run.photos();vectors=[]
    for pid in ids:
        idx=int(photos[pid]['path'].split('-')[0]);idx=1 if idx==10 else idx
        v=np.zeros(16,dtype='float32');v[idx]=1;vectors.append(v)
    target=run.root/'cache/test-vectors.npz';target.parent.mkdir(exist_ok=True)
    np.savez(target,ids=np.array(ids),vectors=np.array(vectors))
    run.commit('embed',dict(ids=ids,matrix=str(target.relative_to(run.root)),model={'test_fixture':True}))

def envelope(task):
    return dict(task_id=task['task_id'],reviewer=dict(kind='test_fixture',name='Synthetic mechanical test',session='pytest'),
                seen_photo_ids=task['photo_ids'],seen_artifacts=task['contact_sheets']+task['page_images'],
                evidence_note='自动化测试夹具；只验证状态与接口，不代表真实视觉审阅或选片效果。')

def row(pid,action,related=None):
    return dict(photo_id=pid,action=action,reason_code='test_fixture',reason_text='合成素材测试预设决定，仅验证处理与回溯逻辑。',evidence={'fixture':True},related_photo_ids=related or [],role='narrative')

def auto_answer(run,task,proposal):
    a=envelope(task);stage=task['stage'];ids=task['target_ids']
    if task.get('context',{}).get('phase')=='global_events':
        a['event_groups']=[dict(event_id=e['event_id'],title=e['title'],source_event_ids=[e['event_id']],reason='合成事件全书复核，保持各测试事件独立。') for e in task['context']['events']]
    elif task.get('context',{}).get('phase')=='omission_check':
        a['decisions']=[row(pid,'retain_omitted') for pid in ids]
    elif stage in ['screen','dedup','event_select','select']:
        action=dict(screen='keep',dedup='keep',event_select='candidate',select='select')[stage]
        a['decisions']=[row(pid,action) for pid in ids]
        if stage=='select':a['coverage']=[dict(event_id=e['event_id'],selected_ids=e['photo_ids'],reason='测试夹具保留各事件全部代表。') for e in run.snapshot('cluster')['data']['events']]
    elif stage=='cluster':
        a['events']=[dict(event_id=f'E{i//3+1}',title=f'测试事件 {i//3+1}',photo_ids=ids[i:i+3],reason='合成照片预先定义的测试分组；不代表自动语义识别。',people=[]) for i in range(0,len(ids),3)]
    elif stage=='story':
        selected=run.snapshot('select')['data']['selected'];events=run.snapshot('cluster')['data']['events']
        selected=[p for e in events for p in e['photo_ids']]
        a.update(selected=selected,changes=[],chapters=[dict(title=e['title'],photo_ids=e['photo_ids'],reason='测试事件顺序') for e in events],
                 coverage=[dict(event_id=e['event_id'],selected_ids=e['photo_ids'],reason='保留测试事件所有成员。') for e in events],summary=[dict(text='本书展示用于验证流程的几何测试图片。',photo_ids=selected)])
    elif stage=='layout':
        selected=run.snapshot('story')['data']['selected'];sizes=[1,1,2,2,3,0];templates=['single','full','double','hero_detail','grid','chapter'];pages=[];pos=0
        # Choose a square photo for full-bleed.
        selected.sort(key=lambda p:run.photos()[p]['width']/run.photos()[p]['height']!=1)
        for i,(n,t) in enumerate(zip(sizes,templates)):
            members=selected[pos:pos+n];pos+=n
            pages.append(dict(number=i+1,template=t,title='' if t=='full' else ['序曲','', '相遇','光与形','片段','旅程之后'][i],
                              caption='' if t=='full' else '合成素材，仅用于验证版式与流程。',caption_reason='' if t=='full' else '补充素材为合成测试图片的背景，避免把夹具当成真实旅行。',text_sources=members or selected[:1],reason='检查六种模板及实际页面输出。',
                              items=[dict(photo_id=p,role='hero' if j==0 else 'detail',crop=[0,0,1,1],reason='保持测试图片完整画面。') for j,p in enumerate(members)]))
        a.update(pages=pages,cover_photo_id=selected[0])
    elif stage=='qa':a.update(page_reviews=[dict(number=n,status='pass',reason='自动测试夹具；此状态不代表真人视觉确认。') for n in list(range(1,7))+['cover']],unresolved=['真实旅行选片与用户验收未执行；此处只验证机械流程。'])
    return a

def complete(run,stage):
    folder=prepare(run,stage);batch=read(folder/'batch.json')
    while True:
        task=next((t for t in batch['tasks'] if not (folder/'responses'/f'{t["task_id"]}.json').exists()),None)
        if task is None:
            expanded=extend_context_review(run,folder)
            if len(expanded['tasks'])==len(batch['tasks']):break
            batch=expanded;continue
        answer=auto_answer(run,task,batch['proposal']);path=folder/f'{task["task_id"]}.fixture.json';write(path,answer);submit(run,folder,path)
    return finish(run,folder)
