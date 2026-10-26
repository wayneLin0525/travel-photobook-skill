"""Versioned, album-independent defaults and measurable editorial checks."""
from copy import deepcopy
import math
import re

from .core import require

DESIGN_SYSTEM = 'editorial_v1'
EDITORIAL_SYSTEMS = {'editorial_v4', DESIGN_SYSTEM}  # v4 is the frozen Xinjiang-era name.
DEFAULT_POLICY = dict(version='1.0.0', design_system=DESIGN_SYSTEM,
                      title_max_chars=20, caption_max_chars=40,
                      detail_min_short_edge_mm=36, single_min_axis_fill=.85,
                      group_min_hero_area_ratio=1.5)


def default_policy():
    return deepcopy(DEFAULT_POLICY)


def resolve_answer(answer, policy):
    """New tasks cannot silently select a legacy renderer by omitting a flag."""
    result=deepcopy(answer)
    require(result.get('design_system',policy['design_system'])==policy['design_system'],
            '本排版任务要求默认新版设计系统；旧快照请通过历史恢复功能查看')
    require('layout_policy' not in result or result['layout_policy']==policy,'排版回答不得自行修改任务的设计规则')
    result['design_system']=policy['design_system'];result['layout_policy']=deepcopy(policy)
    return result


def photo_aspects(run,page):
    photos=run.photos();ratios=[]
    for item in page['items']:
        p=photos[item['photo_id']];l,t,r,b=item['crop']
        ratios.append(p['width']*(r-l)/(p['height']*(b-t)))
    return ratios


def adaptive_frames(page,w,h,aspects):
    """Select a reusable composition from count, aspect ratios and image roles."""
    n=len(page['items']);m=14;top=32;gap=8;inner=w-2*m;available=h-56
    if not n:return [],'text_page'
    if page['template']=='full':return [[0,0,w,h]],'full'
    if n==1:return [[m,top,inner,available]],'feature'
    hero=next((i for i,item in enumerate(page['items']) if item['role']=='hero'),0)
    if n==2 and page['template']=='double' and abs(math.log(aspects[0]/aspects[1]))<.25:
        half=(inner-gap)/2
        return [[m,top,half,available],[m+half+gap,top,half,available]],'balanced_pair'
    if n==2:
        main=inner*.62;side=inner-main-gap
        slots=[[m,top,main,available],[m+main+gap,top+available*.37,side,available*.63]]
        name='hero_pair'
    elif aspects[hero]>=1.15:
        main_h=available*.58;detail_h=available-main_h-gap;cell=(inner-gap*(n-2))/(n-1)
        slots=[[m,top,inner,main_h]]+[[m+i*(cell+gap),top+main_h+gap,cell,detail_h] for i in range(n-1)]
        name='wide_hero_with_details'
    else:
        main=inner*.60;side=inner-main-gap;cell=(available-gap*(n-2))/(n-1)
        slots=[[m,top,main,available]]+[[m+main+gap,top+i*(cell+gap),side,cell] for i in range(n-1)]
        name='portrait_hero_with_details'
    # Slot assignment follows roles, not file order. Source images/crops are unchanged.
    order=[hero]+[i for i in range(n) if i!=hero];frames=[None]*n
    for i,frame in zip(order,slots):frames[i]=frame
    return frames,name


def fitted_rect(frame,aspect):
    x,y,w,h=frame;iw=min(w,h*aspect);ih=iw/aspect
    return [x+(w-iw)/2,y+(h-ih)/2,iw,ih]


def assess_plan(run,plan):
    """Measure actual contained photos, not just the nominal boxes; fail with a fix."""
    if plan.get('design_system')!=DESIGN_SYSTEM:return None
    from .layout import page_frames
    policy=plan.get('layout_policy',default_policy());pages=[]
    w,h=run.config['width_mm'],run.config['height_mm'];body_w=w-28;body_h=h-56
    for page in plan['pages']:
        n=page['number'];items=page['items'];exception=page.get('text_exception_reason','')
        for field in ['title','caption','location','date']:
            require(isinstance(page.get(field,''),str),f'第{n}页的{field}必须是文字')
        require(len(page.get('title',''))<=policy['title_max_chars'] or len(exception)>=8,f'第{n}页标题过长，请缩短或记录必要性')
        require(len(page.get('caption',''))<=policy['caption_max_chars'] or len(exception)>=8,f'第{n}页图注过长，请删去重复描述或记录必要性')
        require(not re.search(r'[|｜/／]',' '.join(page.get(k,'') for k in ['title','caption','location','date'])) or len(exception)>=8,
                f'第{n}页文字含结构性分隔符；地点独立填写，日期用月和日表示')
        if page.get('caption'):
            require(len(page.get('caption_reason',''))>=8,f'第{n}页图注必须说明画面外的必要背景；不需要则留空')
        aspects=photo_aspects(run,page);frames=page_frames(page,w,h,DESIGN_SYSTEM,aspects)
        composition='custom' if page.get('frames_mm') is not None else adaptive_frames(page,w,h,aspects)[1]
        rects=[fitted_rect(frame,aspect) for frame,aspect in zip(frames,aspects)]
        hero_ids=[i for i,item in enumerate(items) if item['role']=='hero']
        if items:require(len(hero_ids)==1,f'第{n}页必须明确一张主图；成对等重关系也需指定阅读起点')
        equal_sequence=page.get('composition')=='equal_sequence'
        if equal_sequence:
            require(len(page.get('composition_reason',''))>=12,f'第{n}页等大组照必须说明连续动作或对照关系')
        hero_area_ratio=None
        if len(items)==1 and page['template']!='full':
            axis=max(rects[0][2]/body_w,rects[0][3]/body_h)
            require(axis>=policy['single_min_axis_fill'],f'第{n}页单图被压小；请扩大实际展示宽度或高度')
        if len(items)>1:
            hero=hero_ids[0];areas=[r[2]*r[3] for r in rects];other=max(a for i,a in enumerate(areas) if i!=hero)
            hero_area_ratio=areas[hero]/other
            minimum=1.0 if len(items)==2 else policy['group_min_hero_area_ratio']
            require(equal_sequence or hero_area_ratio>=minimum-.01,f'第{n}页主辅照片缺少层级，主图/最大辅图面积比为{hero_area_ratio:.2f}；请调整布局或把照片移到相关页面')
            for i,rect in enumerate(rects):
                if i==hero:continue
                short=min(rect[2:])
                require(short>=policy['detail_min_short_edge_mm'] or len(items[i].get('small_detail_reason',''))>=8,
                        f'第{n}页辅图{items[i]["photo_id"]}实际尺寸过小，短边{short:.1f}毫米；请重新分配页面，不能用缩小照片解决容量')
        pages.append(dict(number=n,composition=composition,hero_area_ratio=hero_area_ratio,
                          photos=[dict(photo_id=item['photo_id'],role=item['role'],frame_mm=frame,actual_rect_mm=rect,
                                       small_detail_reason=item.get('small_detail_reason','')) for item,frame,rect in zip(items,frames,rects)],
                          caption_reason=page.get('caption_reason',''),text_exception_reason=exception,
                          composition_reason=page.get('composition_reason','')))
    return dict(policy=deepcopy(policy),status='passed',pages=pages,
                note='程序检查尺寸、层级和文字结构；必要性、情感、叙事和裁切仍须视觉审阅。')
