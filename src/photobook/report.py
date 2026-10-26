from __future__ import annotations
import csv
import html
import json
from pathlib import Path
from urllib.parse import quote
from .core import STAGES, read, write

LABELS=dict(scan='初筛指标',screen='初筛复核',embed='本地模型',dedup='去重',locate='地点确认',cluster='事件聚类',event_select='事件内精选',select='全书精选',story='故事检查',layout='排版',render='PDF输出',qa='页面复核')

def report(run):
    photos={p['photo_id']:p for p in run.snapshot('scan',False)['data']['photos']};records=[];snaps=[]
    for stage in STAGES:
        for path in sorted((run.root/'stages'/stage).glob('*/snapshot.json')):
            snap=read(path);snaps.append(snap)
            for d in snap['decisions']:
                records.append(dict(d,revision=snap['revision'],is_current=run.heads().get(stage)==snap['revision']))
    records.sort(key=lambda d:d['timestamp'])
    rows=[]
    for pid,p in photos.items():
        history=[r for r in records if r['photo_id']==pid]
        rows.append(dict(photo_id=pid,path=p['path'],preview=p.get('preview'),original=(run.source/p['path']).as_uri(),history=history))
    event_map={}
    for snap in snaps:
        if snap['stage']=='cluster' and run.heads().get('cluster')==snap['revision']:
            event_map={p:e['event_id'] for e in snap['data']['events'] for p in e['photo_ids']}
    location_map={}
    if 'locate' in run.heads():location_map={r['photo_id']:r for r in run.snapshot('locate',False)['data']['records']}
    payload=dict(event_map=event_map,location_map=location_map,title=run.config['title'],test_mode=bool(run.config.get('test_mode')),status=run.status(),labels=LABELS,photos=rows,heads=run.heads(),
                 stages=[dict(stage=s['stage'],revision=s['revision'],timestamp=s['timestamp'],data=s['data']) for s in snaps if run.heads().get(s['stage'])==s['revision']])
    # JSON escaped against </script>, and DOM updates exclusively use textContent.
    data=json.dumps(payload,ensure_ascii=False)
    for char,code in [('<','003c'),('>','003e'),('&','0026')]:
        data=data.replace(char,chr(92)+'u'+code)
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>照片书流程审阅</title><link rel="icon" href="data:,">
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#263c3b;background:#f5f3ed}*{box-sizing:border-box}body{margin:0}header{padding:32px 4vw 20px;background:#e6e9df;border-bottom:1px solid #ccd3c4}h1{margin:6px 0;font-size:30px;font-weight:600}small{color:#62726c}nav{display:flex;gap:9px;flex-wrap:wrap;margin:18px 0}.chip{padding:6px 12px;border-radius:18px;background:#fff;font-size:12px}.stale{background:#f7d7b5}.pending{opacity:.6}.tools{position:sticky;top:0;background:#f5f3edf5;padding:16px 4vw;display:flex;gap:12px;z-index:2;flex-wrap:wrap}input,select,button{padding:9px;border:1px solid #bcc6be;border-radius:6px;background:white;color:#263c3b}main{padding:12px 4vw 40px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:20px}.card{background:white;border:1px solid #e1e4dc;border-radius:9px;padding:13px}.photo{width:100%;height:200px;object-fit:contain;background:#f1f0eb}.title{font-size:13px;overflow-wrap:anywhere}.reason{font-size:13px;line-height:1.7}details{font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#376e64}dialog{width:min(1100px,95vw);max-height:90vh;border:0;border-radius:12px;padding:24px}dialog::backdrop{background:#182823aa}.compare{display:flex;gap:15px}.compare img{width:48%;max-height:60vh;object-fit:contain}.outputs{display:flex;gap:20px;flex-wrap:wrap;margin:12px 0}#summary{margin-bottom:22px;line-height:1.8}.count{margin-left:auto}article{border-top:1px solid #ddd;padding:12px 0}.error{color:#9b4030}
</style><header><small>PHOTO BOOK / 可回溯制作记录</small><h1 id="title"></h1><p>照片与报告保存在本地。此页展示真实处理记录；待审阅环节不会显示为完成。</p><nav id="stages"></nav><div class="outputs" id="outputs"></div></header>
<div class="tools"><select id="stage"><option value="">全部阶段</option></select><select id="event"><option value="">全部事件</option></select><select id="city"><option value="">全部城市/区域</option></select><select id="reason"><option value="">全部原因</option></select><select id="action"><option value="">全部决定</option></select><input id="search" placeholder="搜索照片编号、文件名、理由"><label><input id="current" type="checkbox" checked>仅当前版本</label><span id="count" class="count"></span></div>
<main><div id="summary"></div><div id="grid" class="grid"></div></main><dialog id="detail"><button id="close">关闭</button><div id="body"></div></dialog><script id="data" type="application/json">PAYLOAD</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent),$=s=>document.querySelector(s);
function el(tag,text,cls){let e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function image(p){let i=el('img');i.className='photo';if(p.preview)i.src=p.preview;i.alt=p.photo_id;return i}
$('#title').textContent=D.title;if(D.test_mode)$('#title').after(el('p','测试夹具模式：审阅结论为预设测试数据，不代表真实照片的模型精选结果。','error'));
for(const [s,status] of Object.entries(D.status)){let display=status;let entry=D.stages.find(x=>x.stage===s);if(s==='qa'&&entry&&status==='complete')display=entry.data.visual_status;let span=el('span',(D.labels[s]||s)+' · '+display,'chip '+status);$('#stages').append(span);let o=el('option',D.labels[s]||s);o.value=s;$('#stage').append(o)}
const LM=D.location_map||{};for(const city of [...new Set(Object.values(LM).map(x=>x.city).filter(Boolean))].sort()){let o=el('option',city);o.value=city;$('#city').append(o)}
const all=D.photos.flatMap(p=>p.history),events=[...new Set(all.map(x=>x.event_id).filter(Boolean))],reasons=[...new Set(all.map(x=>x.reason_code))];
for(const [target,values] of [['#event',events],['#reason',reasons],['#action',[...new Set(all.map(x=>x.action))]]])for(const v of values){let o=el('option',v);o.value=v;$(target).append(o)}
const render=D.stages.find(s=>s.stage==='render');if(render){for(const [key,label] of [['preview_pdf','阅读预览'],['design_pdf','原图质量设计稿'],['cover_pdf','封面设计稿'],['overview','跨页总览']]){let a=el('a',label);a.href=render.data[key];$('#outputs').append(a)}$('#outputs').append(el('small','印刷规格尚未验证；查看 render 的警告'))}
const stageData=s=>D.stages.find(x=>x.stage===s)?.data;const screen=stageData('screen'),dedup=stageData('dedup'),selection=stageData('story')||stageData('select'),layout=stageData('layout');
$('#summary').append(el('strong','输入 '+D.photos.length+' 个文件'+(screen?' → 初筛 '+screen.active.length:'')+(dedup?' → 去重 '+dedup.active.length:'')+(selection?' → 入书 '+selection.selected.length:'')+(layout?' / '+layout.pages.length+' 页内页':'')+'。'));
$('#summary').append(el('p','在“全部决定”里选择 select 可只看最终入选，选择 merge 可检查去重代表；每张照片可查看完整历史和比较依据。'));
for(const s of D.stages){let det=el('details');det.append(el('summary',(D.labels[s.stage]||s.stage)+' · 计数、证据与本阶段结果'));let pre=el('pre',JSON.stringify(s.data,null,2));det.append(pre);$('#summary').append(det)}
function history(p,records){$('#body').replaceChildren(el('h2',p.photo_id+' · '+p.path));let a=el('a','打开原始图片');a.href=p.original;$('#body').append(a,el('p','原图位置：'+decodeURI(p.original)),image(p));if(LM[p.photo_id])$('#body').append(el('p','地点：'+LM[p.photo_id].label),el('pre',JSON.stringify(LM[p.photo_id],null,2)));for(const r of records){let art=el('article');art.append(el('strong',(D.labels[r.stage]||r.stage)+' / '+r.action+' / '+r.timestamp),el('p',r.reason_text));if(r.related_photo_ids?.length){let compare=el('div',undefined,'compare');compare.append(image(p));for(const id of r.related_photo_ids){let other=D.photos.find(p=>p.photo_id===id);if(other){compare.append(image(other));art.append(el('p','比较/代表：'+id))}}art.append(compare)}art.append(el('pre',JSON.stringify(r,null,2)));$('#body').append(art)}$('#detail').showModal()}
function update(){let grid=$('#grid');grid.replaceChildren();let count=0;for(const p of D.photos){let rr=p.history.filter(r=>(!$('#current').checked||r.is_current)&&(!$('#stage').value||r.stage===$('#stage').value)&&(!$('#event').value||(r.event_id||D.event_map[p.photo_id])===$('#event').value)&&(!$('#reason').value||r.reason_code===$('#reason').value)&&(!$('#action').value||r.action===$('#action').value));if($('#city').value&&LM[p.photo_id]?.city!==$('#city').value)continue;let text=p.path+p.photo_id+JSON.stringify(rr)+JSON.stringify(LM[p.photo_id]||{});if(!rr.length||!text.toLowerCase().includes($('#search').value.toLowerCase()))continue;count++;let card=el('div',undefined,'card');let r=rr[rr.length-1];card.append(image(p),el('h3',p.photo_id+' · '+p.path,'title'),el('small',(D.labels[r.stage]||r.stage)+' / '+r.action),el('p',r.reason_text,'reason'));if(LM[p.photo_id])card.append(el('p','地点：'+LM[p.photo_id].label,'reason'));let b=el('button','查看历史与比较');b.onclick=()=>history(p,p.history);card.append(b);grid.append(card)}$('#count').textContent=count+' 张'}
for(const s of ['#stage','#event','#city','#reason','#action','#search','#current'])$(s).addEventListener('input',update);$('#close').onclick=()=>$('#detail').close();update();
</script></html>'''.replace('PAYLOAD',data)
    (run.root/'report.html').write_text(page,'utf8')
    write(run.root/'audit.json',payload)
    with (run.root/'decisions.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['photo_id','stage','revision','is_current','action','reason_code','reason_text','event_id','page_id','related_photo_ids','supersedes']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for r in records:writer.writerow(r)
    return run.root/'report.html'
