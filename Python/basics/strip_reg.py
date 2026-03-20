import re

def my_strip(s: str, chars: str | None = None) -> str:
    if chars is None:
        return re.sub(r'^\s+|\s+$', '', s)
    cls = re.escape(chars)
    return re.sub(rf'^[{cls}]+|[{cls}]+$', '', s)


if __name__ == '__main__':
    print(my_strip('   spacious   '))
    print(my_strip('www.example.com', 'cmowz.'))
    print(my_strip('#....... Section 3.2.1 Issue #32 .......', '.#! '))
