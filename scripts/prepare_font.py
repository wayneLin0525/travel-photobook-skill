"""Build-time only: pin, verify, and instantiate the licensed CJK font."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from photobook.assets import download
from photobook.runtime import RESOURCES

COMMIT = 'f8d157532fbfaeda587e826d4cd5b21a49186f7c'
URL = f'https://raw.githubusercontent.com/notofonts/noto-cjk/{COMMIT}/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf'
SHA256 = 'd68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964'

def main():
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    path = download(URL, RESOURCES / 'fonts/NotoSansSC-VF.ttf', SHA256)
    font = TTFont(path)
    instantiateVariableFont(font, {'wght': 400}, inplace=True).save(RESOURCES / 'fonts/NotoSansSC-Regular.ttf')
    print(RESOURCES / 'fonts/NotoSansSC-Regular.ttf')

if __name__ == '__main__':
    main()
