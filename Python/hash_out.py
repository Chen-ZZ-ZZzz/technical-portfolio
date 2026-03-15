"""5) Extract hashtags but not inside words
Text: "We love #python, but not C# or abc#def. Also #привет is ok."
Goal: ["python", "привет"]
Hints: word boundaries don’t work perfectly with #. Use lookarounds:
not preceded by word char
# then letters/numbers/underscore in Unicode"""
def hash_out(s: str) -> list:
    # that’s a very real findall() “gotcha”, list of that group
    return re.findall(r"(?<!\w)#(\w+)", s)
