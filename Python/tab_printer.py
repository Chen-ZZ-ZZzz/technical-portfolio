"""takes a list of lists of strings and displays it
in a well-organized table with each column right-justified.
Assume that all the inner lists will contain the same
number of strings.
"""

# def printTable(inTable):
#     colWidths = [0] * len(inTable)
#     numRows = len(inTable[0])
#     newTable = [''] * numRows

#     for j in range(numRows):
#         for i, iList in enumerate(inTable):
#             colWidths[i] = len(max(iList, key=len))
#             newTable[j] += ' ' + iList[j].rjust(colWidths[i])
#         print(newTable[j])
""" above is bad written because i calculated column width
everytime through the inner loop. that makes O(C-R²) instead
of twice O(C-R) below.
    also newTable[] is useless
"""

def printTable(inTable):
    # two passes
    # first to get format numbers
    colWidths = [max(len(item) for item in col) for col in inTable]
    numRows = len(inTable[0])

    # through the parameter once again to print re-aligned lines
    for i in range(numRows):
        row = []
        for j, col in enumerate(inTable):
            row.append(col[i].rjust(colWidths[j]))
        print(" ".join(row))

    # # one pass? makes no sense for this function
    # colWidths = [0] * len(inTable)
    # rows = []

    # for row in zip(*inTable):          # walks through the table once
    #     rows.append(row)               # buffer for later printing
    #     for c, item in enumerate(row):
    #         if len(item) > colWidths[c]:
    #             colWidths[c] = len(item)

    # for row in rows:                   # print using the widths we learned
    #     print(" ".join(item.rjust(colWidths[c]) for c, item in enumerate(row)))

tableData = [['apples', 'oranges', 'cherries', 'banana'],
             ['Alice', 'Bob', 'Carol', 'David'],
             ['dogs', 'cats', 'moose', 'goose']]

printTable(tableData)
