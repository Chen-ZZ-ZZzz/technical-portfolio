from pathlib import Path
import re

REP = {
    'ADJECTIVE': 'Enter an adjective: ',
    'NOUN':      'Enter a noun: ',
    'ADVERB':    'Enter an adverb: ',
    'VERB':      'Enter a verb: ',
}

rep_pat = re.compile(r'\b(?:ADJECTIVE|NOUN|ADVERB|VERB)\b')


def mad_libs(textf: str | Path) -> None:
    path = Path(textf)
    text = path.read_text(encoding='utf-8')
    print(text)

    def repl(match: re.Match) -> str:
        return input(REP[match.group(0)])

    new_text = rep_pat.sub(repl, text)
    print(new_text)

    out_path = path.parent / f'{path.stem}_new.txt'
    out_path.write_text(new_text, encoding='utf-8')


if __name__ == '__main__':
    mad_libs('test/madlib_test.txt')
