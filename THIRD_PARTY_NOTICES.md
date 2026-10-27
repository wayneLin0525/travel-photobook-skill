# Third-party resources and release conditions

Travel Photo Book source is licensed under GNU AGPL-3.0-only; see LICENSE.
Its dependencies and bundled resources retain their own licenses below.
Runtime distributions include the project license and a corresponding-source
link. Do not describe the complete runtime bundle as MIT.

- PDF processing: PyMuPDF / MuPDF, AGPL or a separately obtained commercial
  license. https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- Local image encoder: OpenAI CLIP ViT-B/32, MIT. Pinned upstream checkpoint is
  verified before loading; the license is in src/photobook/resources/LICENSE.CLIP.txt.
  https://github.com/openai/CLIP
- Previous development snapshots used MobileCLIP2 under apple-amlr. Those weights
  and private caches are not part of this project or its default installer.
- Chinese font: Noto Sans SC, SIL Open Font License 1.1. The build freezes the
  variable weight axis at 400; the original license is retained alongside the font.
  https://github.com/notofonts/noto-cjk
- Offline page turns: StPageFlip / page-flip 2.0.7, MIT. The license and provenance
  are preserved in src/photobook/web/vendor and embedded into final HTML.
- Pillow-HEIF: BSD-3-Clause; libheif and codec components have their own notices.
  Runtime builds collect installed package licenses for review.
- OpenCLIP, PyTorch, Pillow, NumPy, OpenCV, ReportLab, and other dependencies retain
  their upstream terms. Installed package notices accompany runtime bundles.

The runtime license directory is internal software distribution material. Users
receive their PDF, offline HTML and spread overview image; embedded resources retain the notices they need.
