"""Build a portable skill ZIP, optionally with WorkBuddy marketplace metadata."""
import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['generic', 'workbuddy'], default='generic')
    parser.add_argument('--author', help='Required for WorkBuddy marketplace metadata')
    args = parser.parse_args()
    if args.platform == 'workbuddy' and not args.author:
        parser.error('WorkBuddy市场包需要真实作者名称')
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp) / 'travel-photobook'
        shutil.copytree(ROOT / 'skills/travel-photobook', folder)
        shutil.copy2(ROOT / 'LICENSE', folder / 'LICENSE')
        if args.platform == 'workbuddy':
            import yaml
            path = folder / 'SKILL.md'; text = path.read_text(encoding='utf-8')
            _, header, body = text.split('---', 2)
            data = yaml.safe_load(header)
            data.update(display_name='旅行照片书', display_name_en='Travel Photo Book',
                        description_zh=data['description'], description_en='Turn a travel photo folder into a curated PDF and an offline flipbook HTML.',
                        version='0.2.0', author=args.author)
            path.write_text('---\n' + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + '---' + body, encoding='utf-8')
        destination = ROOT / 'dist' / f'travel-photobook-0.2.0-{args.platform}.zip'
        destination.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    z.write(path, path.relative_to(folder.parent).as_posix())
        print(destination)

if __name__ == '__main__':
    main()
