from pathlib import Path

import pymupdf as fitz
from PIL import Image
import pytest

from photobook.core import WorkflowError
from photobook.overview import build_overview


def colored_pdf(path, colors):
    with fitz.open() as doc:
        for color in colors:
            page=doc.new_page(width=210,height=297)
            page.draw_rect(page.rect,color=color,fill=color)
        doc.save(path)


def test_overview_pixels_follow_cover_and_real_book_parity(tmp_path):
    pdf=tmp_path/'book.pdf';dest=tmp_path/'overview.jpg'
    colors=[(1,0,0),(0,1,0),(0,0,1),(1,1,0),(1,0,1)]
    colored_pdf(pdf,colors);result=build_overview(pdf,dest)
    with Image.open(dest) as image:
        # Cover, blank–1, 2–3, 4–blank. Sample page centers, away from
        # labels and borders, to catch mislabeled or repeated page images.
        checks=[((605,320),(255,0,0)),((1490,320),(238,236,230)),
                ((2060,320),(0,255,0)),((320,964),(0,0,255)),
                ((890,964),(255,255,0)),((1490,964),(255,0,255)),
                ((2060,964),(238,236,230))]
        for point,expected in checks:
            assert all(abs(a-b)<=4 for a,b in zip(image.getpixel(point),expected))
        assert list(image.size)==result['pixels']


@pytest.mark.parametrize('inner_pages',[2,32])
def test_overview_contains_every_pdf_page_once(tmp_path,inner_pages):
    pdf=tmp_path/'book.pdf';colored_pdf(pdf,[(.3,.5,.7)]*(inner_pages+1))
    result=build_overview(pdf,tmp_path/'overview.jpg')
    assert [n for spread in result['spreads'] for n in spread['pdf_pages']]==list(range(1,inner_pages+2))
    assert result['spreads'][0]['label']=='封面'
    assert result['spreads'][-1]['pdf_pages']==[inner_pages+1]


def test_overview_rejects_wrong_page_count_before_writing(tmp_path):
    pdf=tmp_path/'book.pdf';colored_pdf(pdf,[(.3,.5,.7)]*4)
    with pytest.raises(WorkflowError,match='偶数内页'):
        build_overview(pdf,tmp_path/'overview.jpg')
    assert not (tmp_path/'overview.jpg').exists()
