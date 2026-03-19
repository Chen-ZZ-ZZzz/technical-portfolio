"""
Find all directories that:
contain no files
and no subdirectories

Output:
list of Path objects
sorted lexicographically
"""

def empty_dir(base):
    for root, dirs, files in Path(base).walk():
        try:
            # print(dirs, files)
            if not dirs and not files:
                yield root
        except PermissionError:
            print(f"You Don't Have the Permission to {root}")
            continue                # I want to skip. not sure if that's right

empty_dir_data = sorted(empty_dir(q), key=str) # this does not creat new list
print(empty_dir_data)
