import hashlib
import io
from pathlib import Path
from urllib.error import URLError

import pytest

from photobook.assets import download,model_asset
from photobook.core import WorkflowError
from photobook.metadata import read_metadata, coordinates
from photobook.runtime import cache_root, exiftool_command, platform_tag
from tests.fixtures import make_photos


def test_doctor_rejects_missing_tls_runtime_resource(monkeypatch):
    from photobook.driver import doctor
    def missing_policy(*args, **kwargs):
        raise FileNotFoundError('missing TLS trust policy')
    monkeypatch.setattr('ssl.create_default_context', missing_policy)
    result = doctor()
    assert result['state'] == 'ENVIRONMENT_MISSING'
    check = next(c for c in result['checks'] if c['name'] == 'tls_context')
    assert not check['ok'] and 'missing TLS trust policy' in check['error']


def test_embedded_exif_needs_no_executable(tmp_path, monkeypatch):
    source = make_photos(tmp_path / 'photos', 300)
    monkeypatch.setenv('PATH', '')
    data, version, warnings = read_metadata(source)
    item = data[str((source / '01-测试.jpg').resolve())]
    assert item['DateTimeOriginal'] == '2026:05:01 10:00:00'
    assert item['Make'] == 'Fixture' and version == 'pillow-exif-v1'
    assert data[str((source / '08-测试.jpg').resolve())]['Orientation'] == 6
    assert data[str((source / '09-测试.heic').resolve())]['DateTimeOriginal']
    assert '11-corrupt.jpg' in warnings


def test_gps_signs_and_unknown():
    assert coordinates({1: 'S', 2: (30, 30, 0), 3: 'W', 4: (100, 15, 0)}) == {'GPSLatitude': -30.5, 'GPSLongitude': -100.25}
    assert coordinates({}) == {}


def test_download_resumes_and_verifies_before_install(tmp_path, monkeypatch):
    raw = b'resource bytes'; digest = hashlib.sha256(raw).hexdigest()
    target = tmp_path / 'weights.bin'; target.with_suffix('.bin.part').write_bytes(raw[:4])
    class Response(io.BytesIO):
        status = 206
        headers = {'Content-Range': f'bytes 4-{len(raw)-1}/{len(raw)}'}
    requests = []
    def get(request, **kwargs):
        requests.append(request); return Response(raw[4:])
    monkeypatch.setattr('urllib.request.urlopen', get)
    assert download('https://example.invalid/model', target, digest).read_bytes() == raw
    assert requests[0].get_header('Range') == 'bytes=4-'
    assert download('https://example.invalid/model', target, digest) == target
    assert len(requests) == 1
    target.write_bytes(b'bad')
    with pytest.raises(WorkflowError, match='损坏'):
        download('https://example.invalid/model', target, digest)


def test_bad_download_never_becomes_ready(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        status = 200; headers = {}
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: Response(b'bad'))
    target = tmp_path / 'weights.bin'
    with pytest.raises(WorkflowError, match='失败'):
        download('https://example.invalid/model', target, '0' * 64)
    assert not target.exists()


def test_cache_override_and_windows_exiftool_path(tmp_path, monkeypatch):
    monkeypatch.setenv('PHOTOBOOK_CACHE', str(tmp_path))
    assert cache_root() == tmp_path
    exe = tmp_path / 'tools/exiftool.exe'; exe.parent.mkdir(); exe.write_bytes(b'fixture')
    monkeypatch.setattr('photobook.runtime.sys.platform', 'win32')
    assert exiftool_command() == [str(exe)]


def test_release_model_does_not_overwrite_legacy_provenance(tmp_path):
    from photobook.core import write
    path=tmp_path/'model-provenance.json';write(path,dict(model='MobileCLIP2-S0',weights=[]));original=path.read_bytes()
    with pytest.raises(WorkflowError,match='旧模型'):
        model_asset(tmp_path)
    assert path.read_bytes()==original
