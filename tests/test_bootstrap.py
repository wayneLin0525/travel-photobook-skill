"""Installer mechanics with a tiny shell stub, never a real photo/model result."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from photobook.runtime import platform_tag

pytestmark=pytest.mark.skipif(sys.platform=='win32',reason='POSIX launcher test; Windows launcher runs in runtime CI')
ROOT=Path(__file__).resolve().parents[1]


def setup(tmp_path):
    skill=tmp_path/'技能 空格';(skill/'scripts').mkdir(parents=True)
    script=skill/'scripts/photobook.sh';shutil.copy2(ROOT/'skills/travel-photobook/scripts/photobook.sh',script)
    env=dict(os.environ,PHOTOBOOK_CACHE=str(tmp_path/'cache'),PHOTOBOOK_ALLOW_LOCAL_RELEASE='1',PATH='/usr/bin:/bin')
    env.pop('PHOTOBOOK_ENGINE',None)
    return skill,script,env


def test_installer_does_not_claim_ready_without_release(tmp_path):
    _,script,env=setup(tmp_path)
    result=subprocess.run(['/bin/sh',str(script),'doctor'],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and '尚未配置' in result.stderr


def test_native_bootstrap_and_cached_reuse_without_python(tmp_path):
    skill,script,env=setup(tmp_path)
    archive=tmp_path/'runtime.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('photobook-engine/photobook-engine','#!/bin/sh\nprintf \'{"state":"READY","fixture":true}\\n\'\n')
    prefix=skill/'assets/runtime'/platform_tag();prefix.parent.mkdir(parents=True)
    prefix.with_suffix('.url').write_text(archive.as_uri())
    prefix.with_suffix('.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
    result=subprocess.run(['/bin/sh',str(script),'doctor'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==dict(state='READY',fixture=True)
    archive.unlink()
    result=subprocess.run(['/bin/sh',str(script),'doctor'],env=env,capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['fixture']


def test_hash_failure_never_executes_or_keeps_partial_install(tmp_path):
    skill,script,env=setup(tmp_path)
    archive=tmp_path/'bad.zip';archive.write_bytes(b'wrong file')
    prefix=skill/'assets/runtime'/platform_tag();prefix.parent.mkdir(parents=True)
    prefix.with_suffix('.url').write_text(archive.as_uri());prefix.with_suffix('.sha256').write_text('0'*64)
    result=subprocess.run(['/bin/sh',str(script),'doctor'],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and '校验失败' in result.stderr
    assert not list(Path(env['PHOTOBOOK_CACHE']).rglob('.ready'))
    assert not list(Path(env['PHOTOBOOK_CACHE']).rglob('.install-*'))
