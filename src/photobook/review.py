from __future__ import annotations
import math
import re
import uuid
from pathlib import Path
import numpy as np
from .core import STAGES, WorkflowError, require, fingerprint, read, write, now
from .images import contact_sheets, delta_seconds, distance_km, time_value
from .design_policy import DESIGN_SYSTEM, default_policy, resolve_answer, assess_plan

PROMPTS = {
 'screen': '逐张查看照片。技术风险只作提示，夜景、剪影、虚化和唯一瞬间不得机械淘汰。对每张目标给 keep 或 exclude、具体原因和证据。',
 'dedup': '相似度仅用于候选检索。逐张比较主体、动作、表情和构图。keep 或 merge；merge 必须直接指向本批上下文中实际比较过且最终保留的照片。相同背景不同活动不合并。不确定则保留。',
 'cluster': '检查全部联系表及组间边界，修正初分事件。时间、地点未知时不要猜。每张照片恰好属于一个事件；可以拆分或合并提议组。事件标题描述可见活动。',
 'event_select': '在本事件挑选代表图及互补照片。candidate、alternate 或 drop，每张说明理由。新增照片需解释增加了什么信息。角色 hero/narrative/detail。普通事件1至3张只是起点。',
 'select': '在容量范围内构成全书入选集。select 或 alternate，每张说明理由。先满足必选、事件和人物覆盖，再增加互补照片。逐事件说明份额。超出数量范围必须写明原因。不得按单一分数取前N张。',
 'story': '查看入选顺序与落选总览，检查事件和人物遗漏、重复、顺序与事实依据。可以补回、替换。写有照片证据的概括，所有变化逐张解释。禁止编造身份、地点、内心活动。',
 'layout': '按章节和故事顺序安排完整页序。本任务默认editorial_v1，未填写设计系统也采用新版，禁止退回旧模板。single/full/double/hero_detail/grid/chapter按横竖比例及主辅角色自动选取通用布局，默认无需逐页写坐标。每页明确一张主图；相同照片组有连续动作或对照必要时可声明equal_sequence及composition_reason。标题简短，location与date独立；caption仅补充必要背景并填写caption_reason，不复述画面，不堆叠竖线斜杠。章节页照片不缩小。四图默认主辅布局，实际辅图太小必须调页，不能硬塞。可使用frames_mm作可回溯覆盖；裁切须保全主体。所有文字和地点注明证据，每页说明理由，遵守任务layout_policy。',
 'qa': '检查实际PDF的每张页面渲染：裁切、文字、主次、留白、页序、图片是否正确。结合design_checks核对实际尺寸；图注是否补充必要背景、是否只在复述画面、等大组照理由是否成立，仍须看图判断。必要性说明不等于审阅通过。逐页返回pass或issue，问题含修正建议。不能依据源JSON代替看页面。印刷状态仍为待验证。'
}

def matrix(run):
    d = run.snapshot('embed')['data']
    with np.load(run.root / d['matrix'], allow_pickle=False) as z:
        return {pid: v for pid, v in zip(z['ids'].tolist(), z['vectors'])}

def dedup_proposal(run):
    photos = run.photos(); ids = run.snapshot('screen')['data']['active']; must = run.must_ids()
    ordered = sorted(ids, key=lambda p: (p not in must, photos[p]['path']))
    seen, canonical, exact = {}, [], {}
    for pid in ordered:
        key = photos[pid]['pixel_hash']
        if key in seen and pid not in must: exact[pid] = seen[key]
        else: canonical.append(pid); seen.setdefault(key,pid)
    vec = matrix(run); x = np.stack([vec[p] for p in canonical]); k = run.config['neighbor_count']
    hashes={p:int(photos[p]['phash'],16) for p in canonical}
    devices={p:tuple(photos[p]['metadata'].get(key) for key in ('Make','Model','SerialNumber')) for p in canonical}
    times={p:time_value(photos[p]) for p in canonical}
    pairs = {}
    def add(a,b,why,sim):
        if a == b: return
        key = tuple(sorted([a,b])); entry = pairs.setdefault(key, dict(ids=list(key), signals=[], similarity=round(float(sim),6)))
        if why not in entry['signals']: entry['signals'].append(why)
    # Blocked similarities avoid an unbounded N x N allocation.
    for begin in range(0,len(canonical),128):
        scores = x[begin:begin+128] @ x.T
        for i,row in enumerate(scores):
            idx = begin+i; a = canonical[idx]
            row[idx] = -2
            count = min(k,len(row)-1)
            neighbors = np.argsort(-row,kind='stable')[:count]
            for j in neighbors: add(a,canonical[j],'global_embedding_neighbor',row[j])
            timed = []; hashed = []; nearby=[]
            for j,b in enumerate(canonical):
                if j == idx: continue
                if (hashes[a]^hashes[b]).bit_count() <= run.config['phash_distance']:
                    hashed.append(j)
                device_a,device_b=devices[a],devices[b]
                ta,tb=times[a],times[b]
                dt=abs((ta-tb).total_seconds()) if ta is not None and tb is not None and (ta.tzinfo is None)==(tb.tzinfo is None) else None
                if any(device_a) and device_a == device_b and dt is not None and dt <= run.config['burst_seconds']:
                    timed.append(j)
                if dt is not None and dt<=max(180,run.config['burst_seconds']*3):
                    km=distance_km(photos[a],photos[b])
                    if km is not None and km<=.2:nearby.append(j)
            for j in sorted(hashed,key=lambda j:-row[j])[:run.config['phash_neighbor_limit']]: add(a,canonical[j],'phash_candidate',row[j])
            for j in sorted(timed,key=lambda j:-row[j])[:k]: add(a,canonical[j],'burst_neighbor',row[j])
            # Similar frames can score differently after lighting/viewpoint changes.
            # Timestamp neighbors remain review candidates even with low embedding similarity.
            for j in sorted(timed,key=lambda j:abs((times[a]-times[canonical[j]]).total_seconds()))[:k]:
                add(a,canonical[j],'burst_time_neighbor',row[j])
            for j in sorted(nearby,key=lambda j:abs((times[a]-times[canonical[j]]).total_seconds()))[:k]:
                add(a,canonical[j],'time_gps_neighbor',row[j])
    return dict(canonical=canonical, exact=exact, pairs=list(pairs.values()), candidate_limit_note='每张感知哈希比较最多保留配置数量的候选，防止低纹理图片产生平方级审阅任务；未提出比较的照片仍保留。')

def cluster_proposal(run):
    photos=run.photos(); vec=matrix(run); ids=run.snapshot('dedup')['data']['active']
    known=[p for p in ids if time_value(photos[p]) is not None]
    # ISO strings sort within timezone cohorts, never mix unknown offsets as if UTC.
    known.sort(key=lambda p:(str(time_value(photos[p]).tzinfo),time_value(photos[p]).isoformat(),p))
    groups=[]
    for pid in known:
        reasons=[]; strong_boundary=False
        if groups:
            prev=groups[-1]['photo_ids'][-1]; dt=delta_seconds(photos[prev],photos[pid]); km=distance_km(photos[prev],photos[pid])
            if dt is None or dt>run.config['event_minutes']*60:
                reasons.append('time_gap_or_incomparable_clock');strong_boundary=True
            if km is not None and km>run.config['event_km']:
                reasons.append('location_gap');strong_boundary=True
            if float(vec[prev]@vec[pid])<run.config['scene_similarity']:
                reasons.append('scene_change_candidate')
                if dt is not None and dt>=run.config.get('scene_min_gap_seconds',300):strong_boundary=True
        if not groups or strong_boundary:groups.append(dict(photo_ids=[],boundary_signals=reasons or ['start'],interior_signals=[]))
        elif reasons:groups[-1]['interior_signals'].append(dict(photo_id=pid,signals=reasons))
        groups[-1]['photo_ids'].append(pid)
    # Unknown-date images are visually grouped, explicitly marked tentative.
    unknown=[p for p in ids if p not in set(known)]
    visual=[]
    for pid in unknown:
        match=next((g for g in visual if float(vec[g['photo_ids'][0]]@vec[pid])>=.8),None)
        if match is None:
            match=dict(photo_ids=[],boundary_signals=['unknown_time_visual_group']);visual.append(match)
        match['photo_ids'].append(pid)
    groups.extend(visual)
    for i,g in enumerate(groups):g.update(event_id=f'E{i+1:03}',title='待视觉确认事件',uncertainty='needs_review')
    return dict(events=groups)

def stage_input(run, stage):
    photos=run.photos()
    if stage=='screen':
        ids=[p for p,v in photos.items() if v['state']!='exclude']; return ids,dict(risks={p:photos[p].get('risks',[]) for p in ids})
    if stage=='dedup':
        proposal=dedup_proposal(run);return proposal['canonical'],proposal
    if stage=='cluster':return run.snapshot('dedup')['data']['active'],cluster_proposal(run)
    if stage=='event_select':return run.snapshot('dedup')['data']['active'],run.snapshot('cluster')['data']
    if stage=='select':
        rows=run.snapshot('event_select')['data']['decisions'];return [r['photo_id'] for r in rows if r['action'] in ['candidate','alternate']],dict(events=run.snapshot('cluster')['data']['events'],event_candidates=rows)
    if stage=='story':
        selected=run.snapshot('select')['data']['selected']
        # Include near-merged frames as well as event/global omissions. A mistakenly
        # rejected unique moment must remain visually recoverable, not just logged.
        ids=run.snapshot('screen')['data']['active']
        return ids,dict(selected=selected,events=run.snapshot('cluster')['data']['events'])
    if stage=='layout':return run.snapshot('story')['data']['selected'],run.snapshot('story')['data']
    if stage=='qa':return run.snapshot('story')['data']['selected'],run.snapshot('render')['data']
    raise WorkflowError('不能为此环节创建视觉任务')

def answer_example(stage, ids):
    envelope=dict(task_id='COPY_TASK_ID', reviewer=dict(kind='visual_model',name='实际模型名称',session='当前审阅会话标识'),
                  seen_photo_ids=ids, seen_artifacts=[], evidence_note='填写实际看图过程及原图放大情况')
    row=dict(photo_id=ids[0] if ids else '', action='keep', reason_code='填写原因代码', reason_text='填写具体观察和比较依据',
             evidence={'observed':'填写画面证据'},related_photo_ids=[],role='narrative')
    if stage in ['screen','dedup','event_select','select']:
        if stage=='event_select':row['action']='candidate'
        if stage=='select':row['action']='select'
        envelope['decisions']=[row]
        if stage=='select':envelope.update(coverage=[],quantity_reason='')
    if stage=='cluster':envelope['events']=[dict(event_id='E001',title='事件名称',photo_ids=ids,reason='分组证据',people=[],background='',uncertainty='unknown')]
    if stage=='story':envelope.update(selected=ids,chapters=[dict(title='章节',photo_ids=ids,reason='顺序依据')],changes=[],coverage=[],summary=[dict(text='有证据的事实概括',photo_ids=ids)],quantity_reason='')
    if stage=='layout':envelope.update(design_system=DESIGN_SYSTEM,pages=[dict(number=1,template='single',title='',caption='',caption_reason='',location='',date='',text_sources=[],reason='编排依据',items=[dict(photo_id=ids[0] if ids else '',role='hero',crop=[0,0,1,1],reason='保持完整主体')])],cover_photo_id=ids[0] if ids else '',reuse_reason='')
    if stage=='qa':envelope.update(page_reviews=[],unresolved=[])
    return envelope

def prepare(run, stage):
    require(stage in PROMPTS,'未知视觉环节');run.verify_sources(full=False);deps=run.dependencies(stage)
    ids,proposal=stage_input(run,stage)
    require(ids,'没有可审阅照片')
    batch_id=uuid.uuid4().hex;folder=run.root/'reviews'/stage/batch_id;folder.mkdir(parents=True)
    task_specs=[]
    if stage=='event_select':task_specs=[(e['photo_ids'],e) for e in proposal['events']]
    elif stage=='dedup' and len(ids)<=384:
        # A small album is cheaper and more coherent to compare once globally
        # than to redraw the same nearest neighbours in dozens of batches.
        task_specs=[(ids,{})]
    elif stage in ['screen','dedup']:task_specs=[(ids[i:i+24],{}) for i in range(0,len(ids),24)]
    elif stage=='cluster':
        ordered=[p for e in proposal['events'] for p in e['photo_ids']]
        task_specs=[(ordered[i:i+48],dict(events=[dict(e,photo_ids=[p for p in e['photo_ids'] if p in ordered[i:i+48]])
            for e in proposal['events'] if set(e['photo_ids']) & set(ordered[i:i+48])],
            boundary_ids=ordered[max(0,i-4):i]+ordered[i+48:i+52])) for i in range(0,len(ordered),48)]
    elif stage=='story':
        omitted=[p for p in ids if p not in set(proposal['selected'])]
        task_specs=[(omitted[i:i+24],dict(proposal,phase='omission_check')) for i in range(0,len(omitted),24)]
        if not omitted:task_specs=[(proposal['selected'],dict(proposal,phase='assembly'))]
    else:task_specs=[(ids,proposal)]
    tasks=[];photos=run.photos()
    for i,(targets,extra) in enumerate(task_specs):
        tid=f'{stage}-{i+1:04}'
        context=set(targets)
        if stage=='cluster':context.update(extra.get('boundary_ids',[]))
        if stage=='story' and extra.get('phase')=='omission_check':
            membership={p:e for e in proposal['events'] for p in e['photo_ids']}
            dd={r['photo_id']:r for r in run.snapshot('dedup')['data']['decisions']}
            comparisons={}
            for pid in targets:
                rep=dd[pid].get('related_photo_ids',[pid])[0] if dd[pid]['action']=='merge' else pid
                event=membership[rep]
                refs=[p for p in event['photo_ids'] if p in proposal['selected']][:2]
                comparisons[pid]=dict(event_id=event['event_id'],representative=rep,selected_refs=refs)
                context.update(refs+[rep])
            extra=dict(extra,comparisons=comparisons)
        if stage=='dedup':
            relevant=[p for p in proposal['pairs'] if set(p['ids']) & set(targets)]
            for pair in relevant:context.update(pair['ids'])
            extra=dict(pairs=relevant)
        context=sorted(context,key=lambda pid:photos[pid]['path'])
        sheets=[] if stage=='qa' else contact_sheets(run,context,folder/tid)
        page_images=[]
        if stage=='qa':page_images=proposal['page_images']
        task=dict(task_id=tid,stage=stage,target_ids=targets,photo_ids=context,contact_sheets=sheets,
                  page_images=page_images,photos=[dict(photo_id=p,original=str(run.source/photos[p]['path']),
                    preview=str(run.root/photos[p]['preview']),metadata=photos[p]['metadata'],metrics=photos[p]['metrics']) for p in context],
                  context=extra,prompt=PROMPTS[stage],constraints=run.config,rule_version=run.config['rule_version'])
        if stage=='story' and extra.get('phase')=='omission_check':
            task['prompt']='逐张复看落选图，与本事件入选图或去重代表比较。对target给retain_omitted或reconsider，写明独特情绪、人物、动作或情节是否遗漏。reconsider只是提交全书重新取舍，不自动入选。'
        if run.config.get('location_required') and stage in ['cluster','event_select','select','story','layout','qa']:
            from .locations import location_context
            task['locations']=location_context(run,context)
        if stage=='layout':task['layout_policy']=default_policy()
        write(folder/f'{tid}.json',task);write(folder/f'{tid}.answer.example.json',answer_example(stage,targets))
        tasks.append(task)
    batch=dict(batch_id=batch_id,stage=stage,dependencies=deps,config_hash=run.config_hash(),created_at=now(),
               proposal=proposal,tasks=tasks,context_review_version=2)
    write(folder/'batch.json',batch)
    return folder

def local_events(run, tasks, answers):
    events=[]
    for task,answer in zip(tasks,answers):
        assigned=[p for e in answer.get('events',[]) for p in e.get('photo_ids',[])]
        require(len(assigned)==len(set(assigned)) and set(assigned)==set(task['target_ids']),'本批事件必须无遗漏无重复地覆盖target_ids；边界图只作参考')
        for event in answer.get('events',[]):
            event=dict(event)
            if len(tasks)>1:event['event_id']=task['task_id']+'-'+event.get('event_id','')
            event['_reviewer']=answer['reviewer'];events.append(event)
    combined=[];aliases={}
    for event in events:
        continuation=event.pop('continuation_of',None)
        if continuation:
            require(continuation in aliases,'跨批事件必须引用已审阅的前序事件ID')
            previous=aliases[continuation]
            previous['photo_ids']+=event['photo_ids']
            previous['reason']+='；跨批延续依据：'+event['reason']
            aliases[event['event_id']]=previous
        else:
            combined.append(event);aliases[event['event_id']]=event
    events=combined
    flat=[p for e in events for p in e.get('photo_ids',[])]
    require(len(flat)==len(set(flat)) and set(flat)==set(run.snapshot('dedup')['data']['active']), '事件必须无遗漏无重复地覆盖候选')
    return events


def extend_context_review(run, folder):
    """Append dependent visual tasks only after their actual source replies exist.

    The updated batch lives on disk, so advance/resume and the legacy finish entry
    cannot skip the additional review or assemble from unreviewed suggestions.
    """
    folder=Path(folder);batch=read(folder/'batch.json');stage=batch['stage']
    if batch.get('context_review_version')!=2 or stage not in ('cluster','story'):
        return batch
    phase='global_events' if stage=='cluster' else 'assembly'
    if any(t.get('context',{}).get('phase')==phase for t in batch['tasks']):return batch
    if any(not (folder/'responses'/f'{t["task_id"]}.json').exists() for t in batch['tasks']):return batch
    answers=[read(folder/'responses'/f'{t["task_id"]}.json') for t in batch['tasks']]
    for task,answer in zip(batch['tasks'],answers):check_envelope(task,answer)
    photos=run.photos()
    if stage=='cluster':
        events=local_events(run,batch['tasks'],answers)
        # Opening, middle and closing views expose cross-batch scene continuity.
        ids=list(dict.fromkeys(p for e in events for p in
                 (e['photo_ids'][0],e['photo_ids'][len(e['photo_ids'])//2],e['photo_ids'][-1])))
        context=dict(phase=phase,events=[{k:v for k,v in e.items() if k!='_reviewer'} for e in events])
        prompt='全书复核事件：检查所有事件代表图和时间地点，合并因分批产生的同一连续事件，保留不同活动。event_groups逐项含event_id/title/source_event_ids/reason，source_event_ids恰好覆盖全部已审事件；只能合并整事件，需要拆分则重做cluster。不能仅因同城市合并。'
    else:
        decisions=[dict(row,review_task=a['task_id'],executor=a['reviewer']) for a in answers for row in a.get('decisions',[])]
        reconsider=[r['photo_id'] for r in decisions if r['action']=='reconsider']
        ids=list(dict.fromkeys(batch['proposal']['selected']+reconsider))
        context=dict(batch['proposal'],phase=phase,omission_reviews=decisions,reconsider_ids=reconsider)
        prompt=PROMPTS['story']+' 已逐张复看全部落选照片；本任务只重看原入选及建议补回图，再按全书容量取舍。'
    tid=f'{stage}-{phase}'
    sheets=contact_sheets(run,ids,folder/tid)
    task=dict(task_id=tid,stage=stage,target_ids=ids,photo_ids=ids,contact_sheets=sheets,page_images=[],
        photos=[dict(photo_id=p,original=str(run.source/photos[p]['path']),preview=str(run.root/photos[p]['preview']),
                     metadata=photos[p]['metadata'],metrics=photos[p]['metrics']) for p in ids],
        context=context,prompt=prompt,constraints=run.config,rule_version=run.config['rule_version'])
    if run.config.get('location_required'):
        from .locations import location_context
        task['locations']=location_context(run,ids)
    write(folder/f'{tid}.json',task)
    write(folder/f'{tid}.answer.example.json',answer_example(stage,ids))
    batch['tasks'].append(task);write(folder/'batch.json',batch)
    return batch


def check_envelope(task,answer):
    require(answer.get('task_id')==task['task_id'],'task_id 不匹配')
    reviewer=answer.get('reviewer',{})
    require(reviewer.get('kind') in (['visual_model','human','test_fixture'] if task['constraints'].get('test_mode') else ['visual_model','human']) and reviewer.get('name') and reviewer.get('session'),'缺少实际审阅者/模型及会话信息')
    require(set(task['photo_ids']) <= set(answer.get('seen_photo_ids',[])),'未确认看完本任务全部照片')
    require(set(answer.get('seen_photo_ids',[])) <= set(task['photo_ids']),'看图记录包含任务之外的照片')
    require(set(task['contact_sheets'] + task['page_images']) <= set(answer.get('seen_artifacts',[])),'未确认看完全部联系表或实际PDF页面')
    require(bool(answer.get('evidence_note','').strip()),'缺少实际看图说明')
    if task.get('context',{}).get('phase')=='global_events':
        groups=answer.get('event_groups',[])
        ids=[p for g in groups for p in g.get('source_event_ids',[])]
        expected={e['event_id'] for e in task['context']['events']}
        require(len(ids)==len(set(ids)) and set(ids)==expected,'全书事件复核必须恰好覆盖全部已审事件')
        require(all(g.get('source_event_ids') and g.get('title') and re.fullmatch(r'[A-Za-z0-9_-]+',g.get('event_id',''))
                    and len(g.get('reason','').strip())>=8 for g in groups),'全书事件决定缺少标题、ID或具体依据')
    if task['stage']=='story' and task.get('context',{}).get('phase')=='omission_check':
        decisions=answer.get('decisions',[]);ids=[r.get('photo_id') for r in decisions]
        require(len(ids)==len(set(ids)) and set(ids)==set(task['target_ids']),'落选复核必须逐张给出决定')
        for row in decisions:
            require(row.get('action') in ('retain_omitted','reconsider') and len(row.get('reason_text','').strip())>=8
                    and row.get('evidence') and row.get('reason_code'),'落选复核缺少有效决定或具体证据')
            require(set(row.get('related_photo_ids',[]))<=set(task['photo_ids']),'落选比较图未实际查看')
    if task['stage'] in ['screen','dedup','event_select','select']:
        rows=answer.get('decisions',[]);pids=[r.get('photo_id') for r in rows]
        require(len(pids)==len(set(pids)) and set(pids)==set(task['target_ids']),'每张目标照片必须有且只有一个决定')
        allowed={'screen':['keep','exclude'],'dedup':['keep','merge'],'event_select':['candidate','alternate','drop'],'select':['select','alternate']}[task['stage']]
        for r in rows:
            require(r.get('action') in allowed,'无效 action')
            require(len(r.get('reason_text','').strip())>=8 and r.get('reason_code') and r.get('evidence'),'决定缺少具体理由或证据')
            require(set(r.get('related_photo_ids',[])) <= set(task['photo_ids']),'替代图没有在本任务中实际比较')
            if r['action']=='merge':require(len(r.get('related_photo_ids',[]))==1 and r['photo_id'] not in r['related_photo_ids'],'merge 必须直接指向一个其他代表')


def submit(run, folder, answer_path):
    folder=Path(folder).resolve();require(run.root in folder.parents,'审阅目录不属于本次运行')
    batch=read(folder/'batch.json');run.verify_sources(full=False)
    require(batch['config_hash']==run.config_hash() and batch['dependencies']==run.dependencies(batch['stage']),'审阅输入已过期，请重新 prepare')
    answer=read(answer_path);task=next((t for t in batch['tasks'] if t['task_id']==answer.get('task_id')),None)
    require(task is not None,'未知任务');check_envelope(task,answer)
    path=folder/'responses'/f'{task["task_id"]}.json'
    require(not path.exists(),'该任务已提交；修订请创建新审阅批次，保留旧版')
    write(path,answer)
    return path

def coverage_check(run,answer,selected):
    events=run.snapshot('cluster')['data']['events'];coverage=answer.get('coverage',[])
    require(len(coverage)==len(events) and {c.get('event_id') for c in coverage}=={e['event_id'] for e in events},'必须解释每个事件的覆盖/未入选原因')
    for c in coverage:
        require(bool(c.get('reason')),'覆盖解释为空')
        event=next(e for e in events if e['event_id']==c['event_id'])
        require(set(c.get('selected_ids',[]))==set(event['photo_ids']) & set(selected),'事件覆盖数量与实际选择不一致')
    require(run.must_ids() <= set(selected),'必选照片被遗漏')
    require(len(set(selected))==len(selected) and selected,'入选集为空或重复')
    if not run.config['min_photos']<=len(selected)<=run.config['max_photos']:
        require(len(answer.get('quantity_reason','').strip())>=8,'照片数超出目标范围，必须说明容量取舍')

def finish(run, folder):
    folder=Path(folder).resolve();require(run.root in folder.parents,'审阅目录不属于本次运行');batch=extend_context_review(run,folder);stage=batch['stage'];run.verify_sources(full=False)
    require(batch['config_hash']==run.config_hash() and batch['dependencies']==run.dependencies(stage),'审阅输入已过期')
    answers=[]
    for task in batch['tasks']:
        path=folder/'responses'/f'{task["task_id"]}.json'
        require(path.exists(),f'审阅未完成：{task["task_id"]}')
        a=read(path);check_envelope(task,a);answers.append(a)
    rows=[]
    for a in answers:
        for r in a.get('decisions',[]):
            r=dict(r);r['executor']=a['reviewer'];r['review_task']=a['task_id'];rows.append(r)
    photos=run.photos();proposal=batch['proposal'];a=answers[0];data={}
    if stage=='screen':
        require(run.must_ids() <= {r['photo_id'] for r in rows if r['action']=='keep'},'初筛排除了必选照片')
        data=dict(active=[r['photo_id'] for r in rows if r['action']=='keep'],decisions=rows)
    elif stage=='dedup':
        keep={r['photo_id'] for r in rows if r['action']=='keep'}
        require(run.must_ids() <= keep,'去重不得隐藏用户必选；重复必选保留并报告')
        for r in rows:
            if r['action']=='merge':require(r['related_photo_ids'][0] in keep,'代表图必须直接保留，拒绝链式/循环合并')
        for pid,representative in proposal['exact'].items():
            # Resolve canonical near merge so exact duplicates remain traceable to a surviving image.
            related=next((r['related_photo_ids'][0] for r in rows if r['photo_id']==representative and r['action']=='merge'),representative)
            rows.append(dict(photo_id=pid,action='merge',reason_code='exact_duplicate',reason_text='规范化像素完全相同，保留对应代表图。',
                             related_photo_ids=[related],evidence=dict(pixel_hash=photos[pid]['pixel_hash'],exact_representative=representative)))
        data=dict(active=sorted(keep),exact=proposal['exact'],pairs=proposal['pairs'],decisions=rows)
    elif stage=='cluster':
        local_tasks=[t for t in batch['tasks'] if t.get('context',{}).get('phase')!='global_events']
        local_answers=[x for t,x in zip(batch['tasks'],answers) if t in local_tasks]
        events=local_events(run,local_tasks,local_answers)
        global_answers=[x for t,x in zip(batch['tasks'],answers) if t.get('context',{}).get('phase')=='global_events']
        if batch.get('context_review_version')==2:
            require(len(global_answers)==1,'尚未完成全书事件复核')
            ga=global_answers[0];groups=ga.get('event_groups',[])
            sources=[eid for g in groups for eid in g.get('source_event_ids',[])]
            require(len(sources)==len(set(sources)) and set(sources)=={e['event_id'] for e in events},'全书事件复核必须恰好覆盖全部已审事件')
            lookup={e['event_id']:e for e in events};merged=[]
            for group in groups:
                require(group.get('source_event_ids') and len(group.get('reason','').strip())>=8,'事件合并或保留须有具体依据')
                merged.append(dict(event_id=group.get('event_id'),title=group.get('title'),reason=group['reason'],
                    photo_ids=[p for eid in group['source_event_ids'] for p in lookup[eid]['photo_ids']],
                    source_event_ids=group['source_event_ids'],_reviewer=ga['reviewer']))
            events=merged
        flat=[p for e in events for p in e.get('photo_ids',[])]
        require(len(flat)==len(set(flat)) and set(flat)==set(run.snapshot('dedup')['data']['active']),'事件必须无遗漏无重复地覆盖候选')
        require(len({e.get('event_id') for e in events})==len(events),'事件 ID 重复')
        for e in events:
            require(re.fullmatch(r'[A-Za-z0-9_-]+',e.get('event_id','')) and e.get('photo_ids') and e.get('title') and e.get('reason'),'事件缺少成员、标题或分组依据')
            reviewer=e.pop('_reviewer')
            for pid in e['photo_ids']:rows.append(dict(photo_id=pid,action='assign_event',event_id=e['event_id'],executor=reviewer,reason_code='event_grouping',reason_text=e['reason'],evidence=dict(event=e)))
        data=dict(events=events)
    elif stage=='event_select':
        mapping={p:e['event_id'] for e in proposal['events'] for p in e['photo_ids']}
        for r in rows:r['event_id']=mapping[r['photo_id']]
        candidates={r['photo_id'] for r in rows if r['action'] in ['candidate','alternate']}
        require(run.must_ids() <= candidates,'事件精选排除了必选项')
        require(all(any(r['action']=='candidate' and r['event_id']==e['event_id'] for r in rows) for e in proposal['events']),'每个事件至少要保留一个代表候选')
        data=dict(decisions=rows)
    elif stage=='select':
        membership={pid:e['event_id'] for e in run.snapshot('cluster')['data']['events'] for pid in e['photo_ids']}
        for r in rows:r['event_id']=membership[r['photo_id']]
        selected=[r['photo_id'] for r in rows if r['action']=='select'];coverage_check(run,a,selected)
        data=dict(selected=selected,decisions=rows,coverage=a['coverage'],quantity_reason=a.get('quantity_reason',''))
    elif stage=='story':
        assembly=[x for t,x in zip(batch['tasks'],answers) if t.get('context',{}).get('phase')=='assembly']
        if batch.get('context_review_version')==2:
            require(len(assembly)==1,'尚未完成落选复核后的故事编排')
            a=assembly[0]
            require(set(a.get('selected',[]))<=set(a['seen_photo_ids']),'补回照片必须在故事编排中实际看过')
        omission_rows=[dict(r,executor=x['reviewer'],review_task=x['task_id']) for t,x in zip(batch['tasks'],answers)
                       if t.get('context',{}).get('phase')=='omission_check' for r in x['decisions']]
        rows=[]
        selected=a.get('selected',[]);allowed=set(run.snapshot('screen')['data']['active'])
        require(set(selected)<=allowed,'故事补回仅支持可解码且未被初筛排除的照片；其他照片先重做初筛')
        chapters=a.get('chapters',[]);flat=[p for c in chapters for p in c.get('photo_ids',[])]
        require(flat==selected and all(c.get('title') and c.get('reason') for c in chapters),'章节成员顺序必须与 selected 完全一致')
        # Restoring a near-merged photo requires assigning its representative's event.
        events=run.snapshot('cluster')['data']['events'];original=set(run.snapshot('select')['data']['selected'])
        changes={r['photo_id']:r for r in a.get('changes',[])}
        require(set(changes)==original.symmetric_difference(selected),'每个补回/移除必须写变化理由')
        for pid,r in changes.items():require(len(r.get('reason',''))>=8,'变化理由不完整')
        require(run.must_ids()<=set(selected),'故事检查遗漏必选')
        require(len(selected)==len(set(selected)) and selected,'故事入选重复或为空')
        if not run.config['min_photos']<=len(selected)<=run.config['max_photos']:require(len(a.get('quantity_reason',''))>=8,'故事调整后数量超范围，需要说明')
        # Coverage includes representative event attribution for restored duplicates.
        memberships={p:e['event_id'] for e in events for p in e['photo_ids']}
        dd={r['photo_id']:r for r in run.snapshot('dedup')['data']['decisions']}
        for pid in selected:
            if pid not in memberships:
                rep=dd[pid]['related_photo_ids'][0];memberships[pid]=memberships[rep]
        cov=a.get('coverage',[])
        require(len(cov)==len(events) and {c.get('event_id') for c in cov}=={e['event_id'] for e in events},'故事检查缺少完整事件覆盖')
        for c in cov:require(c.get('reason') and set(c.get('selected_ids',[]))=={p for p in selected if memberships[p]==c['event_id']},'故事覆盖与实际照片不一致')
        for s in a.get('summary',[]):require(s.get('text') and s.get('photo_ids') and set(s['photo_ids'])<=set(selected),'概括必须引用入选照片证据')
        if run.config.get('location_required'):
            from .locations import validate_claims
            for s in a.get('summary',[]):validate_claims(run,s.get('location_claims',[]),s['photo_ids'],s['text'])
        require(a.get('summary'),'缺少有证据的概括')
        for pid in allowed:
            rows.append(dict(photo_id=pid,action='select' if pid in selected else 'alternate',executor=a['reviewer'],
                             reason_code='story_review',reason_text=changes.get(pid,{}).get('reason','故事复核保留原决定；详见精选理由和本次事件覆盖记录。'),evidence=dict(coverage=cov),event_id=memberships.get(pid)))
        data=dict(selected=selected,chapters=chapters,summary=a['summary'],coverage=cov,changes=a.get('changes',[]),quantity_reason=a.get('quantity_reason',''),omission_reviews=omission_rows)
    elif stage=='layout':
        from .layout import validate_plan
        policy=batch['tasks'][0].get('layout_policy')
        try:
            if policy:a=resolve_answer(a,policy)
            validate_plan(run,a)
            checks=assess_plan(run,a)
        except WorkflowError as error:
            write(folder/'validation-errors'/f'{uuid.uuid4().hex}.json',dict(timestamp=now(),stage='layout',
                task_id=batch['tasks'][0]['task_id'],policy=policy,error=str(error),dependencies=batch['dependencies']))
            raise
        checked_photos={(page['number'],p['photo_id']):p for page in checks['pages'] for p in page['photos']} if checks else {}
        for page in a['pages']:
            for index,item in enumerate(page['items']):
                measured=checked_photos.get((page['number'],item['photo_id']),{})
                evidence=dict(template=page['template'],crop=item['crop'],page_reason=page['reason'],
                    frame_mm=measured.get('frame_mm',page.get('frames_mm',[None]*len(page['items']))[index]),
                    actual_rect_mm=measured.get('actual_rect_mm'),title=page.get('title',''),
                    caption=page.get('caption',''),caption_reason=page.get('caption_reason',''),
                    location=page.get('location',''),date=page.get('date',''))
                rows.append(dict(photo_id=item['photo_id'],action='place',page_id=page['number'],executor=a['reviewer'],
                    reason_code='layout_role',reason_text=item['reason'],evidence=evidence))
        data=dict(pages=a['pages'],cover_photo_id=a.get('cover_photo_id'),reuse_reason=a.get('reuse_reason',''))
        for key in ['design_system','layout_policy','cover_title','cover_subtitle','cover_caption']:
            if key in a:data[key]=a[key]
        if checks:data['design_checks']=checks
    elif stage=='qa':
        pages=a.get('page_reviews',[]);numbers=[p.get('number') for p in pages]
        expected=list(range(1,run.config['pages']+1))+['cover']
        require(len(numbers)==len(expected) and set(numbers)==set(expected),'必须审阅封面和全部实际 PDF 页')
        for p in pages:
            require(p.get('status') in ['pass','issue'] and p.get('reason'),'页面检查缺少状态或理由')
            if p['status']=='issue':require(p.get('fix'),'页面问题必须给出修正建议')
        data=dict(page_reviews=pages,visual_status='issues' if any(p['status']=='issue' for p in pages) or a.get('unresolved') else 'passed',
                  unresolved=a.get('unresolved',[]),print_status='unverified')
    counts={}
    for r in rows:counts[r['action']]=counts.get(r['action'],0)+1
    data['counts']=counts;data['review_batch']=str(folder.relative_to(run.root));data['reviewers']=[x['reviewer'] for x in answers]
    return run.commit(stage,data,rows,deps=batch['dependencies'])
