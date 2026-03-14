from pathlib import Path
import re
# words to search for
REP = {'ADJECTIVE': 'Enter an adjective:',
       'NOUN': 'Enter a noun:',
       'ADVERB': 'Enter an adverb:',
       'VERB': 'Enter a verb:'
}
rep_pat = re.compile(r'\b(?:ADJECTIVE|NOUN|ADVERB|VERB)\b')

def mad_libs(textf: str | Path):
    with open(textf, 'r', encoding='utf-8') as f_obj:
        text = f_obj.read()
        print(text)
      
        def repl(match: re.Match) -> str:
            token = match.group(0)
            return input(REP[token])

        new_text = rep_pat.sub(repl, text)

        parts: list[str] = []
        last = 0
        for m in rep_pat.finditer(text):
            parts.append(text[last:m.start()])          # unchanged chunk before token
            token = m.group(0)
            parts.append(input(REP[token]))             # user replacement
            last = m.end()                              # move past the token

        parts.append(text[last:])                       # tail after last match
        new_text = "".join(parts)


        print(new_text)
        with open(Path(textf).parent / f'{Path(textf).stem}_new.txt', 'w', encoding='utf-8') as new_obj:
            new_obj.write(f'{new_text}')

mad_libs('test/madlib_test.txt')
