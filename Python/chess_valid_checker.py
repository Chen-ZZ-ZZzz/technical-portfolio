def isValidChessBoard(board):
    MAX = {'K': 1, 'Q': 1, 'B': 2, 'N': 2, 'R': 2, 'P': 8, 'total': 16}
    black = {k: 0 for k in MAX}
    white = {k: 0 for k in MAX}
    # black = {'K': 0, 'Q': 0, 'B': 0, 'N': 0, 'R': 0, 'P': 0, 'total': 0}
    # white = {'K': 0, 'Q': 0, 'B': 0, 'N': 0, 'R': 0, 'P': 0, 'total': 0}

    for s, p in board.items():
        if len(s) != 2 or len(p) != 2:
            # print('Wrong piece or square name')
            return False
        elif (not s[0] in 'abcdefgh') or (not s[1] in '12345678'):
            # print('All pieces must be on a valid square.')
            return False
        elif (not p[0] in 'bw'):
            # print("The piece names should begin with either a 'w' or a 'b'")
            return False
        # if p[0] == 'b':
        #     black['total'] += 1
        #     black[p[1]] += 1
        # elif p[0] == 'w':
        #     white['total'] += 1
        #     white[p[1]] += 1

    # for j in MAX:
    #     if (black[j] > MAX[j] or white[j] > MAX[j]):
    #         # print('Players have invalid number of pieces')
    #         return False

        target = black if p[0] == 'b' else white
        target['total'] += 1
        if target['total'] > MAX['total']:
            return False

        target[p[1]] += 1
        if target[p[1]] > MAX[p[1]]:
            return False

    return True


print(isValidChessBoard({'a1': 'bK', 'e3': 'wK'}))                  # True
print(isValidChessBoard({'a1': 'bking', 'e3': 'wking'}))            # False too long
print(isValidChessBoard({'a1': 'bK', 'e3': 'wK', 'h9': 'wQ'}))      # False (bad square)
print(isValidChessBoard({'a1': 'bK', 'b2': 'bK', 'e3': 'wK'}))      # False (two black kings)
print(isValidChessBoard({'a1': 'bK', 'e3': 'jK'}))                  # False wrong player
