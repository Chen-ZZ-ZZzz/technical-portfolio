import re

'''2) Validate “simple” German phone numbers
Goal: Write is_phone(s) -> bool for patterns like:
+49 171 1234567
0171-1234567
0711 123456
Reject: letters, double plus, too short.'''

phone_list = ['+49 171 1234567', '0171-1234567', '0711 123456', 'asfahflafa', '++49177213107171', '171196876fa81', '0197912', '+4917612102914', '0711212198672', '+49 0711-09876543']

def is_phone(s: str) -> bool:
    # # my version does not enforce the German trunk “0” rule: International format +49 / 0049: must NOT keep the trunk 0 (so +49 0711… should be rejected)
    # phone_pat = re.compile(r'(?:\A(?:\+|00)49)?[ -]?[0-9]{2,5}[ -]?[0-9]{6,8}')
    # if re.fullmatch(phone_pat, s):
    #     return True
    # else:
    #     return False

    # chatgpt5.2 ver.
    PHONE_RX = re.compile(
        r"""
        ^
        (?:
            (?:\+|00)49[ -]?      # +49 or 0049
            [1-9][0-9]{1,4}[ -]?          # prefix (2–5 digits)
            [0-9]{6,8}               # subscriber (6–8 digits)
          |
            0[0-9]{1,4}[ -]?         # national: trunk 0 + prefix
            [0-9]{6,8}               # subscriber
        )
        $
        """,
        re.VERBOSE
    )
    return PHONE_RX.fullmatch(s) is not None
