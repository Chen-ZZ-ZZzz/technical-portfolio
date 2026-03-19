"""
Write:
def atomic_write_text(path: Path, data: str):
    ...
Requirements:
file must never be left empty or partial
write must be atomic on POSIX
parent directory may or may not exist
encoding must be UTF-8
This is a core industrial pattern."""

import tempfile
def atomic_write_text(path: Path, data: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # try:
    #     path.touch()
    # except FileExistsError:
    #     print("This file exists already.")
    #     break
    # Atomic write pattern must not pre-create or truncate the target file.
    # creates the file if missing — defeating atomic safety.
    # Atomic writing means: The original file stays untouched until replacement.
    dir_fd = os.open(path.parent, os.O_DIRECTORY)
    os.fsync(dir_fd)
    os.close(dir_fd)
    # For full durability on POSIX, you should also fsync the directory
    # This ensures the rename itself is persisted.
    with tempfile.NamedTemporaryFile(
            encoding="utf-8", delete_on_close=False,
            dir=path.parent     # Atomic rename on POSIX works only: within the same filesystem
            # That’s why we use: dir=path.parent
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name     # tmp is a file object, not a path. You need tmp.name.
    tmp_path.replace(path)
"""I write to a temporary file in the same directory, fsync it, and then atomically replace the target path to guarantee the file is never partially written."""
