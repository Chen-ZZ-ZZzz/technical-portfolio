VALID_FILES = set('abcdefgh')
VALID_RANKS = set('12345678')
VALID_COLORS = set('bw')
MAX = {'K': 1, 'Q': 1, 'B': 2, 'N': 2, 'R': 2, 'P': 8, 'total': 16}
VALID_PIECES = set(MAX) - {'total'}


def isValidChessBoard(board: dict) -> bool:
    counts = {'b': {k: 0 for k in MAX}, 'w': {k: 0 for k in MAX}}

    for square, piece in board.items():
        if len(square) != 2 or square[0] not in VALID_FILES or square[1] not in VALID_RANKS:
            return False
        if len(piece) != 2 or piece[0] not in VALID_COLORS or piece[1] not in VALID_PIECES:
            return False

        target = counts[piece[0]]
        target['total'] += 1
        target[piece[1]] += 1
        if target['total'] > MAX['total'] or target[piece[1]] > MAX[piece[1]]:
            return False

    return True


print(isValidChessBoard({'a1': 'bK', 'e3': 'wK'}))                  # True
print(isValidChessBoard({'a1': 'bking', 'e3': 'wking'}))             # False - too long
print(isValidChessBoard({'a1': 'bK', 'e3': 'wK', 'h9': 'wQ'}))      # False - bad square
print(isValidChessBoard({'a1': 'bK', 'b2': 'bK', 'e3': 'wK'}))      # False - two black kings
print(isValidChessBoard({'a1': 'bK', 'e3': 'jK'}))                   # False - wrong color
