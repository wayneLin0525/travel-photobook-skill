"""Test a relocated binary with developer PATH/PYTHONPATH removed.

This is not a claim that a clean desktop host installation has been tested.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as temp:
        relocated = Path(temp) / '运行 空格'
        shutil.copytree(ROOT / 'dist/photobook-engine', relocated)
        env = dict(os.environ)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PHOTOBOOK_FONT', 'PHOTOBOOK_EXIFTOOL'):
            env.pop(key, None)
        env['PATH'] = r'C:\Windows\System32;C:\Windows' if sys.platform=='win32' else '/usr/bin:/bin'
        env['PHOTOBOOK_CACHE'] = str(Path(temp) / 'fresh-cache')
        executable = relocated / ('photobook-engine.exe' if sys.platform=='win32' else 'photobook-engine')
        result = subprocess.run([str(executable), 'doctor'], env=env, capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        assert payload['state']=='READY', payload
        assert str(relocated) in next(c['path'] for c in payload['checks'] if c['name']=='font')
        from PIL import Image
        source=Path(temp)/'测试照片';source.mkdir();Image.new('RGB',(320,240),'#42676a').save(source/'图.png')
        def invoke(*args):
            result=subprocess.run([str(executable),*map(str,args)],env=env,capture_output=True,text=True,encoding='utf-8')
            if result.returncode:raise RuntimeError(result.stderr+result.stdout)
            return json.loads(result.stdout)
        job=invoke('start',source,'--output',Path(temp)/'result/V1','--work-root',Path(temp)/'private')['job']
        scan=invoke('advance',job)
        assert scan['stage']=='scan',scan
        heads=json.loads((Path(job)/'heads.json').read_text(encoding='utf-8'))
        snapshot=json.loads((Path(job)/'stages/scan'/heads['scan']/'snapshot.json').read_text(encoding='utf-8'))
        assert snapshot['implementation'] and snapshot['data']['metadata_reader']=='pillow-exif-v1'
        env['PHOTOBOOK_ALLOW_LOCAL_RELEASE']='1'
        script=ROOT/'dist/local-test-skill/travel-photobook/scripts'/('photobook.ps1' if sys.platform=='win32' else 'photobook.sh')
        launcher=['powershell','-NoProfile','-File',str(script)] if sys.platform=='win32' else ['/bin/sh',str(script)]
        boot=subprocess.run(launcher+['doctor'],env=env,capture_output=True,text=True,encoding='utf-8')
        if boot.returncode:raise RuntimeError(boot.stderr+boot.stdout)
        assert json.loads(boot.stdout)['state']=='READY'
        report = dict(test='relocated executable without developer PATH', result=payload,
                      scan=scan,implementation_recorded=True,
                      bootstrap='passed (local release URL)',
                      clean_os_test=False, desktop_host_test=False)
        (ROOT / 'dist/runtime-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    main()
