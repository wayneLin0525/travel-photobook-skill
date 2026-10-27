"""One image of the cover and every interior spread, rendered from the final PDF."""
from __future__ import annotations

import math
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from .core import file_hash, require
from .runtime import font_path


def build_overview(pdf_path, destination):
    pdf_path, destination = Path(pdf_path), Path(destination)
    edge, gutter, margin, gap, label_height = 560, 10, 40, 40, 44
    tile_width, tile_height = edge * 2 + gutter, edge + label_height
    font = ImageFont.truetype(font_path(), 24)
    with fitz.open(pdf_path) as doc:
        inner_pages = len(doc) - 1
        require(inner_pages >= 2 and inner_pages % 2 == 0, '总览需要封面和偶数内页')
        # PDF index 0 is the cover. Interior 1 is on the right; 2–3 form
        # the first full spread. The final even page stays on the left.
        groups = [('封面', [0]), ('01', [None, 1])]
        for left in range(2, len(doc), 2):
            right = left + 1 if left + 1 < len(doc) else None
            label = f'{left:02}–{right:02}' if right is not None else f'{left:02}'
            groups.append((label, [left, right]))
        rows = math.ceil(len(groups) / 2)
        image = Image.new('RGB', (margin * 2 + tile_width * 2 + gap,
                                  margin * 2 + tile_height * rows + gap * (rows - 1)), '#eeece6')
        draw = ImageDraw.Draw(image)
        spreads = []
        for i, (label, indices) in enumerate(groups):
            x = margin + (i % 2) * (tile_width + gap)
            y = margin + (i // 2) * (tile_height + gap)
            for side, index in enumerate(indices):
                if index is None:
                    continue
                page = doc[index]
                scale = min(edge / page.rect.width, edge / page.rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                with Image.frombytes('RGB', (pix.width, pix.height), pix.samples) as thumb:
                    thumb.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    slot = (tile_width - edge) // 2 if i == 0 else side * (edge + gutter)
                    px, py = x + slot + (edge - thumb.width) // 2, y + (edge - thumb.height) // 2
                    image.paste(thumb, (px, py))
                    draw.rectangle((px, py, px + thumb.width - 1, py + thumb.height - 1), outline='#d5d1c8')
            draw.text((x + tile_width // 2, y + edge + 10), label, font=font, fill='#48504b', anchor='mt')
            spreads.append(dict(label=label, pdf_pages=[n + 1 for n in indices if n is not None]))
        require([n for spread in spreads for n in spread['pdf_pages']] == list(range(1, len(doc) + 1)),
                '跨页总览存在漏页或重复页')
        image.save(destination, 'JPEG', quality=92, subsampling=0, optimize=True)
        pixels = list(image.size)
        image.close()
    return dict(source_pdf_sha256=file_hash(pdf_path), pixels=pixels, spreads=spreads)
