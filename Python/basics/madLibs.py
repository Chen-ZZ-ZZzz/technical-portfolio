"""
madLibs.py — fill in a Mad Libs text file interactively

The input file should contain placeholder tokens in uppercase:
ADJECTIVE, NOUN, ADVERB, VERB

Usage:
    python3 madLibs.py <textfile>

Example:
    python3 madLibs.py story.txt
    → produces story_new.txt with user-supplied words
"""

import argparse
import re
from pathlib import Path

REP = {
    'ADJECTIVE': 'Enter an adjective: ',
    'NOUN':      'Enter a noun: ',
    'ADVERB':    'Enter an adverb: ',
    'VERB':      'Enter a verb: ',
}

rep_pat = re.compile(r'\b(?:ADJECTIVE|NOUN|ADVERB|VERB)\b')


def mad_libs(textf: Path) -> None:
    text = textf.read_text(encoding='utf-8')
    print(text)

    def repl(match: re.Match) -> str:
        return input(REP[match.group(0)])

    new_text = rep_pat.sub(repl, text)
    print(new_text)

    out_path = textf.parent / f'{textf.stem}_new.txt'
    out_path.write_text(new_text, encoding='utf-8')
    print(f'Saved to: {out_path}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Interactive Mad Libs filler.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python3 madLibs.py story.txt'
        )
    )
    parser.add_argument('textfile', type=Path, help='Path to the Mad Libs text file')
    args = parser.parse_args()

    if not args.textfile.is_file():
        parser.error(f"File not found: '{args.textfile}'")

    mad_libs(args.textfile)


if __name__ == '__main__':
    main()
