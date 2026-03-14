from pathlib import Path

"""Write a program that walks through a folder tree and searches for exceptionally large files or folders—say, ones that have a file size of more than 100MB. (Remember that, to get a file’s size, you can use os.path.getsize() from the os module.) Print these files with their absolute path to the screen."""

h = Path.home() / "iwish/downloads/films"
BIG = 100 * 1024 * 1024         # 100 MB

def dir_size(path: Path) -> int:
    """Return total size (in bytes) of all files under path."""
    total = 0

    for entry in path.rglob("*"):
        if not entry.is_file():
            continue
        try:
            total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue

    return total

# for file in h.rglob("*"):
for f in h.iterdir():
    try:  # gpt5.2 guard against permission errors
        if f.is_file():      # gpt5.2 teached me to explicitly checking
            size = f.stat(follow_symlinks=False).st_size
        elif f.is_dir():
            size = dir_size(f)
        else:
            continue
    except OSError:
        continue

    if size > BIG:
        print(f"{size / 1024 / 1024:.1f} MB, {f}")

print("Done")
