"""Build on the target OS. Never uploads, installs globally, or invents release URLs."""
from __future__ import annotations
import argparse
import importlib.metadata
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from photobook.core import file_hash, write
from photobook.runtime import RESOURCES, platform_tag


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-base-url', help='Actual HTTPS asset directory, e.g. a published version URL')
    parser.add_argument('--local-manifest', action='store_true', help='Create a private skill copy for local installation testing')
    args = parser.parse_args()
    if not (RESOURCES / 'fonts/NotoSansSC-Regular.ttf').is_file():
        subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_font.py')], check=True)
    build = ROOT / 'build'; build.mkdir(exist_ok=True)
    write(RESOURCES/'build-provenance.json',dict(version='0.2.1',platform=platform_tag(),
        source_sha256={p.name:file_hash(p) for p in sorted((ROOT/'src/photobook').glob('*.py'))}))
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(build / 'pyinstaller-cache')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'photobook-engine',
               '--paths', str(ROOT / 'src'), '--distpath', str(ROOT / 'dist'), '--workpath', str(build / 'engine'),
               '--specpath', str(build), '--add-data', f'{RESOURCES}:photobook/resources',
               '--add-data', f'{ROOT / "src/photobook/web"}:photobook/web', '--collect-data', 'open_clip',
               '--collect-data', 'timm', '--copy-metadata', 'open_clip_torch', '--copy-metadata', 'torch',
               '--copy-metadata', 'pillow', '--copy-metadata', 'reportlab', '--copy-metadata', 'pymupdf',
               '--hidden-import', 'photobook.driver', '--hidden-import', 'photobook.review',
               '--hidden-import', 'photobook.layout', '--hidden-import', 'photobook.models',
               '--hidden-import', 'photobook.publish', '--exclude-module', 'pytest',
               '--exclude-module', 'tkinter', '--exclude-module', 'matplotlib',
               '--exclude-module','pymupdf.layout','--exclude-module','pymupdf4llm',
               '--exclude-module','onnxruntime']
    # Some Python distributions load an additional trust-policy file when
    # constructing SSLContext. Preserve it beside the frozen ssl module.
    trust_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if trust_policy.is_file():
        command.extend(['--add-data', f'{trust_policy}:.'])
    # New torchvision wheels use _C_stable; the stock hook still names _C.
    vision=Path(importlib.util.find_spec('torchvision').origin).parent
    for extension in vision.glob('_C*'):
        if extension.suffix in ('.so','.pyd','.dll','.dylib'):
            command.extend(['--add-binary',f'{extension}:torchvision'])
    command.append(str(ROOT / 'scripts/engine_entry.py'))
    subprocess.run(command, check=True, cwd=ROOT)
    folder = ROOT / 'dist/photobook-engine'
    # Keep licenses in the runtime, never as a third user photo-book artifact.
    licenses = folder / 'licenses'; licenses.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', licenses)
    shutil.copy2(ROOT / 'LICENSE', licenses / 'LICENSE.travel-photobook')
    (folder / 'CORRESPONDING_SOURCE.txt').write_text(
        'Travel Photo Book 0.2.1 — GNU AGPL-3.0-only\n'
        'Source and build instructions: https://github.com/wayneLin0525/travel-photobook-skill/tree/v0.2.1\n'
        'Source archive: https://github.com/wayneLin0525/travel-photobook-skill/archive/refs/tags/v0.2.1.zip\n'
        'Dependency versions: runtime-environment.json; dependency notices: licenses/\n', encoding='utf-8')
    distributions = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        distributions.append(dict(name=name, version=distribution.version))
        for relative in distribution.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in relative.parts):
                path = Path(distribution.locate_file(relative))
                if path.is_file():
                    dest = licenses / name / str(relative).replace('..', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, dest)
    write(folder / 'runtime-environment.json', dict(platform=platform_tag(), python=sys.version, packages=distributions))
    archive = ROOT / 'dist' / f'photobook-engine-0.2.1-{platform_tag()}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(folder.parent).as_posix())
    digest = file_hash(archive)
    write(archive.with_suffix('.json'), dict(filename=archive.name, sha256=digest, bytes=archive.stat().st_size,
                                            platform=platform_tag(), version='0.2.1', model_included=False))
    if args.release_base_url:
        if not args.release_base_url.startswith('https://'):
            parser.error('release-base-url必须为真实HTTPS地址')
        skill = ROOT / 'skills/travel-photobook'
        url = args.release_base_url.rstrip('/') + '/' + archive.name
    elif args.local_manifest:
        skill = ROOT / 'dist/local-test-skill/travel-photobook'
        shutil.copytree(ROOT / 'skills/travel-photobook', skill, dirs_exist_ok=True)
        url = archive.as_uri()
    else:
        skill = None
    if skill:
        prefix = skill / 'assets/runtime' / platform_tag(); prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix('.url').write_text(url + '\n', encoding='utf-8')
        prefix.with_suffix('.sha256').write_text(digest + '\n', encoding='utf-8')
    print(json.dumps(dict(archive=str(archive), sha256=digest, bytes=archive.stat().st_size,
                          skill=str(skill) if skill else None), ensure_ascii=False))

if __name__ == '__main__':
    main()
