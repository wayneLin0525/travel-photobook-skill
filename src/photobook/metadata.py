"""Portable EXIF reader for supported still images; no system Perl or executable."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import ExifTags, Image, ImageCms
import pillow_heif

pillow_heif.register_heif_opener()
READER_VERSION = 'pillow-exif-v1'


def scalar(value):
    if isinstance(value, bytes):
        return value.rstrip(b'\0').decode('utf-8', errors='replace')
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def coordinates(gps):
    def angle(key, ref):
        values = gps.get(key)
        if values is None or len(values) != 3:
            return None
        result = sum(float(v) / d for v, d in zip(values, (1, 60, 3600)))
        direction = scalar(gps.get(ref, ''))
        return -result if direction in ('S', 'W') else result
    lat, lon = angle(2, 1), angle(4, 3)
    return {} if lat is None or lon is None else dict(GPSLatitude=lat, GPSLongitude=lon)


def read_metadata(source):
    result = {}; warnings = []
    tags = {271: 'Make', 272: 'Model', 274: 'Orientation', 36867: 'DateTimeOriginal',
            37521: 'SubSecTimeOriginal', 36881: 'OffsetTimeOriginal', 42033: 'SerialNumber'}
    for path in sorted(Path(source).rglob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.heic', '.heif'}:
            continue
        record = {'SourceFile': str(path.resolve())}
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                nested = exif.get_ifd(ExifTags.IFD.Exif) if exif else {}
                values = dict(exif); values.update(nested)
                record.update({name: scalar(values[tag]) for tag, name in tags.items() if tag in values})
                # pillow-heif normalizes orientation while opening; retain the raw
                # original in metadata, while open_photo uses the normalized pixels.
                if image.info.get('original_orientation') is not None:
                    record['Orientation'] = image.info['original_orientation']
                if exif:
                    record.update(coordinates(exif.get_ifd(ExifTags.IFD.GPSInfo)))
                profile = image.info.get('icc_profile')
                if profile:
                    parsed = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                    record['ProfileDescription'] = ImageCms.getProfileDescription(parsed).strip()
        except (OSError, ValueError, TypeError, ZeroDivisionError, SyntaxError) as error:
            warnings.append(f'{path.name}: {type(error).__name__}: {error}')
        result[str(path.resolve())] = record
    return result, READER_VERSION, '\n'.join(warnings)
