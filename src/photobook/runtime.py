"""OS resource discovery. Does not install packages into a user's system Python."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

RESOURCES = Path(__file__).parent / 'resources'


def cache_root():
    if os.environ.get('PHOTOBOOK_CACHE'):
        return Path(os.environ['PHOTOBOOK_CACHE']).expanduser().resolve()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Caches'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'travel-photobook'


def platform_tag():
    machine = platform.machine().lower()
    arch = {'aarch64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}.get(machine, machine)
    system = {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, sys.platform)
    return f'{system}-{arch}'


def font_path():
    override = os.environ.get('PHOTOBOOK_FONT')
    if override:
        return str(Path(override).expanduser().resolve())
    for path in (RESOURCES / 'fonts/NotoSansSC-Regular.ttf', cache_root() / 'fonts/NotoSansSC-Regular.ttf'):
        if path.is_file():
            return str(path)
    # A development fallback only. Release builds must carry the same open font on every OS.
    path = Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    return str(path) if path.is_file() else str(RESOURCES / 'fonts/NotoSansSC-Regular.ttf')


def resolve_font(value):
    from .core import require
    if value.startswith('resource:'):
        require(value=='resource:NotoSansSC-Regular','未知字体资源ID')
        return font_path()
    return str(Path(value).expanduser().resolve())


def exiftool_command():
    from .core import WorkflowError, require
    override = os.environ.get('PHOTOBOOK_EXIFTOOL')
    if override:
        path = Path(override).expanduser().resolve()
        require(path.is_file(), 'PHOTOBOOK_EXIFTOOL 指定的文件不存在')
        return [str(path)]
    name = 'exiftool.exe' if sys.platform == 'win32' else 'exiftool'
    for base in (RESOURCES / 'tools', cache_root() / 'tools'):
        path = base / name
        if path.is_file():
            perl = base / 'perl/bin/perl'
            if sys.platform != 'win32' and perl.is_file():
                return [str(perl), str(path)]
            return [str(path)]
    found = shutil.which(name)
    if found:
        return [found]
    raise WorkflowError('运行包缺少 ExifTool 及其运行依赖；请重新准备运行包，不能跳过元数据阶段')
