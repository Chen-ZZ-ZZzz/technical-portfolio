# import re

# # my wrong try
# def is_strong(pword:str) -> bool:
    # # TODO 3 regex for 3 rules
    # is_8 = re.compile(r'.{8}')  # at least 8 chars
    # has_digit = re.compile(r'\d+') # at least 1 digit
    # has_lower = re.compile(r'[a-z]+') # at least 1 lowercase
    # has_upper = re.compile(r'[A-Z]+') # at least 1 uppercase

    # test = [is_8, has_digit, has_lower, has_upper]

    # for i in range(4):
    #     if test[i].search(pword) is None:
    #         print('''Your password is too weak!
    #         It must be at least eight characters long,
    #         contain both uppercase and lowercase characters,
    #         and have at least one digit.''')
    #         break

    # print('Your password is strong')

#     rules = [
#         (re.compile(r".{8,}"), "at least 8 characters long"),
#         (re.compile(r"[0-9]"), "contain at least one digit"),
#         (re.compile(r"[a-z]"), "contain at least one lowercase letter"),
#         (re.compile(r"[A-Z]"), "contain at least one uppercase letter"),
#     ]

#     # get all weak points
#     failed = [msg for rx, msg in rules if rx.search(pword) is None]

#     if failed:
#         print("Your password is too weak! It must:")
#         for msg in failed:
#             print(f"- {msg}")
#         return False

#     print("Your password is strong") # good when the password passed all crits
#     return True

# is_strong('123456008')
# is_strong('212aA')
# is_strong('asfafaaäfjafaf')
# is_strong('afafnvh113124')
# is_strong('AUXqplfavf1239')     # true

"""Write a function that takes a string and does the same thing as the strip() string method. If no other arguments are passed other than the string to strip, then the function should remove whitespace characters from the beginning and end of the string. Otherwise, the function should remove the characters specified in the second argument to the function."""

import re

# # this is mine after the help of chatgpt
# def my_strip(to_strip: str, to_remove: str | None = None) -> str:
#     new_string = ''

#     # if no second argument, remove whitespaces at the beginning and the end
#     if to_remove is None:
#         rm_pat = re.compile(r'^\s+|\s+$')
#     else:
#         rm = re.escape(to_remove) # escape regex special chars This is useful if you want to match an arbitrary literal string like urls
#         # print(rm_pat)
#         rm_pat = re.compile(rf'^[{rm}]+|[{rm}]+$')

#     new_string = rm_pat.sub('', to_strip)
#     return new_string


# this is chatgpt 5.2 version
def my_strip(s: str, chars: str | None = None) -> str:
    if chars is None:
        return re.sub(r'^\s+|\s+$', '', s)

    cls = re.escape(chars)  # escape regex-special chars like . ] ^ -
    return re.sub(rf'^[{cls}]+|[{cls}]+$', '', s)

print(my_strip('   spacious   ')) # well returns are silent unless you print() it! or otherwise use them
print(my_strip('www.example.com', 'cmowz.'))
print(my_strip('#....... Section 3.2.1 Issue #32 .......', '.#! '))
