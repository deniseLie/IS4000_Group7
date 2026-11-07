"""Build the packaged PDF font with the optional fontbuild dependencies.

Download NotoSansSC[wght].ttf and OFL.txt from google/fonts/ofl/notosanssc. This pinned source is
SHA-256 a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da. Keep OFL.txt beside the
generated app/assets/fonts/NotoSansSC-Regular.ttf. Run with explicit --source and --destination paths.
The font is embedded/subsetted in each PDF; clients need no system font or network access.
"""
import argparse
from hashlib import sha256
from pathlib import Path


def build(source: Path,destination: Path) -> None:
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    if sha256(source.read_bytes()).hexdigest()!='a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da':
        raise ValueError('Noto source differs from the reviewed open-source font; review its licence and checksum before rebuilding')
    with TTFont(source,recalcTimestamp=False) as original:
        regular=instantiateVariableFont(original,{'wght':400},inplace=False)
        destination.parent.mkdir(parents=True,exist_ok=True)
        regular.save(destination);regular.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();build(args.source,args.destination)
