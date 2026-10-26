from __future__ import annotations
import io
import math
import uuid
from pathlib import Path
from collections import Counter

import pymupdf as fitz
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
from .core import require, write
from .images import open_photo
from .design_policy import DESIGN_SYSTEM, EDITORIAL_SYSTEMS, adaptive_frames, photo_aspects, assess_plan

CAPACITY={'single':(1,1),'full':(1,1),'double':(2,2),'hero_detail':(2,2),'grid':(3,4),'chapter':(0,1)}

def page_frames(page,w,h,design_system=None,aspects=None):
    if page.get('frames_mm') is not None:
        require(design_system in EDITORIAL_SYSTEMS,'自定义图片区域必须声明设计系统')
        return page['frames_mm']
    if design_system==DESIGN_SYSTEM:
        return adaptive_frames(page,w,h,aspects or [.75]*len(page['items']))[0]
    if design_system!='editorial_v4' or page['template']=='full':
        return boxes(page['template'],len(page['items']),w,h)
    m=14;top=32;inner=w-2*m;available=h-56;gap=8
    if page['template'] in ['single','chapter']:return [(m,top,inner,available)] if page['items'] else []
    if page['template']=='double':return [(m,top,(inner-gap)/2,available),(m+(inner+gap)/2,top,(inner-gap)/2,available)]
    if page['template']=='hero_detail':return [(m,top,inner*.63,available),(m+inner*.63+gap,top+available*.37,inner*.37-gap,available*.63)]
    if len(page['items'])==3:return [(m,top,inner*.63,available),(m+inner*.63+gap,top,inner*.37-gap,(available-gap)/2),(m+inner*.63+gap,top+(available+gap)/2,inner*.37-gap,(available-gap)/2)]
    return boxes(page['template'],len(page['items']),w,h)


def validate_frames(page,w,h,design_system,aspects=None):
    frames=page_frames(page,w,h,design_system,aspects)
    require(len(frames)==len(page['items']),'每张照片必须有且只有一个图片区域')
    for frame in frames:
        require(len(frame)==4 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in frame),'图片区域必须是四个有限数字')
        x,y,fw,fh=frame
        require(x>=0 and y>=0 and fw>0 and fh>0 and x+fw<=w+.001 and y+fh<=h+.001,'图片区域超出页面')
        if design_system in EDITORIAL_SYSTEMS and page['template']!='full':
            require(x>=14 and y>=31 and x+fw<=w-14+.001 and y+fh<=h-24+.001,'图片区域侵入标题、图注或页脚保留区')
    for i,a in enumerate(frames):
        for b in frames[i+1:]:
            overlap=min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0])>.001 and min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1])>.001
            require(not overlap,'图片区域相互覆盖')

def validate_plan(run,plan):
    pages=plan.get('pages',[]);selected=set(run.snapshot('story')['data']['selected'])
    require(plan.get('design_system') is None or plan['design_system'] in EDITORIAL_SYSTEMS,'未知设计系统')
    require([p.get('number') for p in pages]==list(range(1,run.config['pages']+1)),'页面数量或顺序不符合容量')
    uses=[]
    for p in pages:
        require(p.get('template') in CAPACITY and p.get('reason'),'模板或排版理由缺失')
        low,high=CAPACITY[p['template']];items=p.get('items',[])
        require(low<=len(items)<=high,'模板图片数量不匹配')
        require(len({i.get('photo_id') for i in items})==len(items),'同页不得重复照片')
        for item in items:
            require(item.get('photo_id') in selected and item.get('reason'),'页面含未入选图片或缺少理由')
            require(item.get('role') in ['hero','narrative','detail'],'图片角色不合法')
            box=item.get('crop',[])
            require(len(box)==4 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in box),'裁切必须是四个有限数字')
            require(0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1,'裁切坐标无效')
            uses.append(item['photo_id'])
        aspects=photo_aspects(run,p) if plan.get('design_system')==DESIGN_SYSTEM else None
        validate_frames(p,run.config['width_mm'],run.config['height_mm'],plan.get('design_system'),aspects)
        text=bool(p.get('title') or p.get('caption') or p.get('location') or p.get('date'))
        require(not text or p.get('text_sources'),'图文页面必须注明文字证据')
        require(set(p.get('text_sources',[]))<=selected,'文字证据引用未知照片')
        if run.config.get('location_required'):
            from .locations import validate_claims
            validate_claims(run,p.get('location_claims',[]),p.get('text_sources',[]),' '.join(p.get(k,'') for k in ['title','caption','location','date']))
        require(p['template']!='full' or not text,'满版模板不叠加文字，请使用其他模板')
    require(set(uses)==selected,'存在入选照片未排入内页')
    require(max(Counter(uses).values(),default=0)<=1 or len(plan.get('reuse_reason',''))>=8,'内页复用照片必须说明必要性')
    require(plan.get('cover_photo_id') in selected,'封面必须引用入选照片')
    assess_plan(run,plan)


def boxes(template,n,w,h):
    # Coordinates are millimetres, top-left origin. Two-page rhythm is assessed in the overview.
    m=16;gap=7;top=30;bottom=23;inner=w-2*m;available=h-top-bottom
    if template=='single':return [(m,top,inner,available)]
    if template=='full':return [(0,0,w,h)]
    if template=='double':return [(m,top,(inner-gap)/2,available),(m+(inner+gap)/2,top,(inner-gap)/2,available)]
    if template=='hero_detail':return [(m,top,inner*.63,available),(m+inner*.63+gap,top+available*.38,inner*.37-gap,available*.62)]
    if template=='grid':
        if n==3:return [(m,top,inner*.6,available),(m+inner*.6+gap,top,inner*.4-gap,(available-gap)/2),(m+inner*.6+gap,top+(available+gap)/2,inner*.4-gap,(available-gap)/2)]
        return [(m+(j%2)*(inner+gap)/2,top+(j//2)*(available+gap)/2,(inner-gap)/2,(available-gap)/2) for j in range(4)]
    return [(m,h*.43,inner,h*.4)] if n else []


def font_setup(path):
    from .runtime import resolve_font
    path=resolve_font(path)
    require(Path(path).is_file(),f'中文字体不存在：{path}，请配置可嵌入的 TrueType 字体')
    pdfmetrics.registerFont(TTFont('BookFont',path))


def fitted_image(run,item,frame,full=False):
    p=run.photos()[item['photo_id']];im=open_photo(run.source/p['path'])
    l,t,r,b=item['crop'];bounds=(round(l*im.width),round(t*im.height),round(r*im.width),round(b*im.height))
    im=im.crop(bounds);require(min(im.size)>0,'裁切后图片为空')
    x,y,w,h=frame
    if full:
        require(abs((im.width/im.height)/(w/h)-1)<.02,'满版裁切比例与页面不符：请明确修改 crop，禁止隐式裁切')
    scale=min(w/im.width,h/im.height)
    iw,ih=im.width*scale,im.height*scale
    x+=(w-iw)/2;y+=(h-ih)/2
    ppi=min(im.width/(iw/25.4),im.height/(ih/25.4))
    return im,(x,y,iw,ih),ppi


def print_image(im, rect, target_ppi):
    """Downsample excess pixels only; preserve the original source and never upscale."""
    from PIL import Image as PILImage
    _, _, width_mm, height_mm = rect
    max_size = (max(1,math.ceil(width_mm / 25.4 * target_ppi)),
                max(1,math.ceil(height_mm / 25.4 * target_ppi)))
    result = im.copy()
    if result.width > max_size[0] or result.height > max_size[1]:
        result.thumbnail(max_size, PILImage.Resampling.LANCZOS)
    ppi = min(result.width / (width_mm / 25.4), result.height / (height_mm / 25.4))
    return result, ppi


def wrap_text(text,size,width):
    lines=['']
    for ch in text:
        if ch=='\n':lines.append('');continue
        if pdfmetrics.stringWidth(lines[-1]+ch,'BookFont',size)>width:
            lines.append(ch)
        else:lines[-1]+=ch
    return lines


def draw_text(c,text,x,y,width,size,maxlines,h,color=(.20,.24,.25)):
    lines=wrap_text(text,size,width*mm)
    require(len(lines)<=maxlines, f'文字超出模板空间：{text[:50]}')
    c.setFont('BookFont',size);c.setFillColorRGB(*color)
    for i,line in enumerate(lines):c.drawString(x*mm,(h-y)*mm-i*size*1.5,line)


def render(run):
    run.verify_sources();plan=run.snapshot('layout')['data'];validate_plan(run,plan)
    c=run.config;w,h=c['width_mm'],c['height_mm'];font_setup(c['font'])
    folder=run.root/'output'/uuid.uuid4().hex;folder.mkdir(parents=True)
    design=folder/'book-design.pdf';cover=folder/'cover-design.pdf'
    pdf=canvas.Canvas(str(design),pagesize=(w*mm,h*mm),pageCompression=1)
    pdf.setTitle(c['title']);pdf.setAuthor('Travel Photo Book')
    placements=[];warnings=[];editorial=plan.get('design_system') in EDITORIAL_SYSTEMS
    for page in plan['pages']:
        pdf.setFillColorRGB(.98,.973,.955);pdf.rect(0,0,w*mm,h*mm,fill=1,stroke=0)
        aspects=photo_aspects(run,page) if plan.get('design_system')==DESIGN_SYSTEM else None
        for item,frame in zip(page['items'],page_frames(page,w,h,plan.get('design_system'),aspects)):
            im,rect,ppi=fitted_image(run,item,frame,page['template']=='full')
            x,y,iw,ih=rect
            # Start with the original crop and discard only resolution above print needs.
            source_ppi=ppi
            im,ppi=print_image(im,rect,c['target_ppi'])
            buf=io.BytesIO();im.save(buf,format='JPEG',quality=95,subsampling=0)
            pdf.drawImage(ImageReader(io.BytesIO(buf.getvalue())),x*mm,(h-y-ih)*mm,iw*mm,ih*mm)
            placement=dict(page=page['number'],photo_id=item['photo_id'],crop=item['crop'],frame_mm=list(rect),effective_ppi=round(ppi,1),source_effective_ppi=round(source_ppi,1),embedded_pixels=list(im.size))
            placements.append(placement)
            if ppi<c['target_ppi']-.5:warnings.append(dict(code='low_effective_ppi',**placement))
        if editorial and page['template']!='full':
            draw_text(pdf,page.get('title',''),14,20,w-28,11,1,h)
            if page.get('caption'):draw_text(pdf,page['caption'],14,h-20,w-45,7.5,1,h,color=(.38,.40,.38))
            location=page.get('location','');date=page.get('date','')
            draw_text(pdf,location,14,h-11,w-45,6.8,1,h,color=(.45,.47,.44))
            offset=pdfmetrics.stringWidth(location,'BookFont',6.8)/mm+7 if location else 0
            draw_text(pdf,date,14+offset,h-11,w-45-offset,6.8,1,h,color=(.45,.47,.44))
        elif page['template']=='chapter':
            draw_text(pdf,page.get('title',''),16,45,w-32,22,2,h)
            draw_text(pdf,page.get('caption',''),16,69,w-32,9,3,h)
        else:
            draw_text(pdf,page.get('title',''),16,19,w-32,10,1,h)
            draw_text(pdf,page.get('caption',''),16,h-14,w-45,8,2,h)
        if page['template']!='full':
            pdf.setFont('BookFont',8);pdf.setFillColorRGB(.43,.46,.45)
            pdf.drawRightString((w-(14 if editorial else 16))*mm,(11 if editorial else 10)*mm,f'{page["number"]:02}')
        pdf.showPage()
    pdf.save()
    cp=canvas.Canvas(str(cover),pagesize=(w*mm,h*mm),pageCompression=1)
    cp.setFillColorRGB(.95,.94,.90);cp.rect(0,0,w*mm,h*mm,fill=1,stroke=0)
    if editorial:
        draw_text(cp,plan.get('cover_title',c['title']),14,25,w-28,26,1,h)
        draw_text(cp,plan.get('cover_subtitle',''),14,36,w-28,10,1,h,color=(.43,.46,.42))
        draw_text(cp,plan.get('cover_caption',''),14,h-11,w-28,6.8,1,h,color=(.45,.47,.44))
    else:draw_text(cp,c['title'],16,32,w-32,23,2,h)
    item=dict(photo_id=plan['cover_photo_id'],crop=[0,0,1,1])
    frame=(14,48,w-28,h-70) if editorial else (16,62,w-32,h-83)
    im,rect,ppi=fitted_image(run,item,frame);x,y,iw,ih=rect
    source_ppi=ppi;im,ppi=print_image(im,rect,c['target_ppi'])
    buf=io.BytesIO();im.save(buf,format='JPEG',quality=95,subsampling=0)
    cp.drawImage(ImageReader(io.BytesIO(buf.getvalue())),x*mm,(h-y-ih)*mm,iw*mm,ih*mm)
    cp.save()
    placements.append(dict(page='cover',photo_id=item['photo_id'],crop=item['crop'],frame_mm=list(rect),effective_ppi=round(ppi,1),source_effective_ppi=round(source_ppi,1),embedded_pixels=list(im.size)))
    if ppi<c['target_ppi']-.5:warnings.append(dict(code='low_effective_ppi',page='cover',photo_id=item['photo_id'],effective_ppi=round(ppi,1)))
    # Inspect actual document font streams and dimensions, not just renderer inputs.
    doc=fitz.open(design)
    require(len(doc)==c['pages'],'实际 PDF 页数错误')
    require(all(abs(p.rect.width-w*mm)<.1 and abs(p.rect.height-h*mm)<.1 for p in doc),'实际 PDF 尺寸错误')
    font_records=[]
    for page in doc:
        for f in page.get_fonts(full=True):
            if f[1] == 'ttf':
                blob=doc.extract_font(f[0])[3];require(bool(blob),'中文字体未嵌入');font_records.append(f[3])
    require(font_records,'未找到已嵌入的中文字体')
    page_images=[]
    for i,page in enumerate(doc):
        target=folder/f'page-{i+1:03}.png';page.get_pixmap(dpi=110).save(target);page_images.append(str(target.relative_to(run.root)))
    doc.close()
    with fitz.open(cover) as cd:
        target=folder/'cover.png';cd[0].get_pixmap(dpi=110).save(target);page_images.append(str(target.relative_to(run.root)))
    # A raster preview is explicitly separate from the original-quality design file.
    preview=fitz.open()
    for path in page_images[:-1]:
        page=preview.new_page(width=w*mm,height=h*mm);page.insert_image(page.rect,filename=str(run.root/path))
    preview_path=folder/'book-preview.pdf';preview.save(preview_path);preview.close()
    spreads=[]
    interior_images = page_images[:-1]
    seq=[[interior_images[0]]]+[interior_images[i:i+2] for i in range(1,len(interior_images),2)]
    for i,pair in enumerate(seq):
        imgs=[Image.open(run.root/p).convert('RGB') for p in pair]
        sw=imgs[0].width;sh=imgs[0].height;out=Image.new('RGB',(sw*2+12,sh),'#e6e3dc')
        if len(imgs)==1:out.paste(imgs[0],(sw+12 if i==0 else 0,0))
        else:
            out.paste(imgs[0],(0,0));out.paste(imgs[1],(sw+12,0))
        target=folder/f'spread-{i:03}.jpg';out.save(target,quality=88);spreads.append(str(target.relative_to(run.root)))
        for im in imgs:im.close()
    tiles=[]
    for path in spreads:
        with Image.open(run.root/path) as im:
            im.thumbnail((480,250));tiles.append(im.copy())
    overview=Image.new('RGB',(1000,math.ceil(len(tiles)/2)*280+30),'#eeece6');draw=ImageDraw.Draw(overview)
    for i,im in enumerate(tiles):
        xx=10+(i%2)*500;yy=20+(i//2)*280;overview.paste(im,(xx,yy));draw.text((xx,yy+250),f'Spread {i}',fill='#333333')
    overview.save(folder/'overview.jpg',quality=90)
    run.verify_sources()
    data=dict(design_pdf=str(design.relative_to(run.root)),preview_pdf=str(preview_path.relative_to(run.root)),
              cover_pdf=str(cover.relative_to(run.root)),page_images=page_images,spreads=spreads,
              overview=str((folder/'overview.jpg').relative_to(run.root)),placements=placements,warnings=warnings,
              technical_checks=dict(source_hashes='passed',page_count='passed',page_dimensions='passed',embedded_fonts=sorted(set(font_records)),design_system=plan.get('design_system','legacy')),
              design_checks=assess_plan(run,plan),visual_status='pending',print_status='unverified',print_note='设计文件；未核对印刷商出血、书脊、色彩和PDF/X要求。')
    write(folder/'preflight.json',data)
    from .core import file_hash
    data['file_hashes']={str(p.relative_to(run.root)):file_hash(p) for p in [design,cover]+[run.root/p for p in page_images]}
    return run.commit('render',data)
