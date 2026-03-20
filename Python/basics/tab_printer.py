def printTable(table: list[list[str]]) -> None:
    col_widths = [max(len(item) for item in col) for col in table]
    for row in zip(*table):
        print(" ".join(item.rjust(width) for item, width in zip(row, col_widths)))


tableData = [['apples', 'oranges', 'cherries', 'banana'],
             ['Alice', 'Bob', 'Carol', 'David'],
             ['dogs', 'cats', 'moose', 'goose']]
printTable(tableData)
