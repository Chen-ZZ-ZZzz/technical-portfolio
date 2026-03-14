"""Write a program that finds all files with a given prefix, such as spam001.txt, spam002.txt, and so on, in a single folder and locates any gaps in the numbering (such as if there is a spam001.txt and a spam003.txt but no spam002.txt). Have the program rename all the later files to close this gap.

As an added challenge, write another program that can insert gaps into numbered files (and bump up the numbers in the filenames after the gap) so that a new file can be inserted."""

h = Path.home() / "iwish/prog/python3_boring/test/spam3" # this is the folder that we mess around
h.mkdir(exist_ok=True)

def renumb(p, root):
    p_size = len(p)
    index = 1

    for f in sorted(root.glob(f"{p}*")): # oh right. pattern needs to write like this, not simply glob(p)
        if not f.is_file: continue
        j = f.stem[p_size:]

        if int(j) != index:
            j = str(index).zfill(len(j))
            # print(f"file {f.name} will be renumbered to {"".join([p, j, f.suffix])}") # test
            # f.move("".join([p, j, f.suffix])) # AttributeError: 'PosixPath' object has no attribute 'move'
            f.rename("".join([p, j, f.suffix])) # alright. found it in doc


        index += 1


def insert_gap(prefix: str, directory: Path, start: int, width: int) -> None:
    """
    Insert a numbering gap of size `width` starting at `start`.
    Existing files >= start are shifted upward.
    """
    prefix_len = len(prefix)
    numbered = []

    for path in directory.iterdir():
        if not path.is_file():
            continue
        if not path.stem.startswith(prefix):
            continue

        suffix = path.stem[prefix_len:]
        if not suffix.isdigit():
            continue

        num = int(suffix)
        numbered.append((num, path))

    # Sort descending so we don't overwrite
    numbered.sort(key=lambda x: x[0], reverse=True)

    for num, path in numbered:
        if num < start:
            continue

        new_num = num + width
        zwidth = len(path.stem) - prefix_len
        new_suffix = str(new_num).zfill(zwidth)
        new_name = f"{prefix}{new_suffix}{path.suffix}"

        path.rename(path.with_name(new_name))

renumb("spam", h)
