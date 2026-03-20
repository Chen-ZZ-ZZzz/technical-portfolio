"""
atom_write.py -- Write data to the file safely

Usage:
    python3 atom_write.py <file> <data>

"""

import argparse
import os
import tempfile
from pathlib import Path


def atomic_write_text(file: Path, data: str) -> None:
    """atomically write data to a file"""
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=file.parent
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    # atomic swap temp to the file
    try:
        tmp_path.replace(file)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    # fsync directory for rename is on disk
    dir_fd = os.open(file.parent, os.O_DIRECTORY)
    os.fsync(dir_fd)
    os.close(dir_fd)


def main():
    parser = argparse.ArgumentParser(description="atomic text writer")
    parser.add_argument("file", help="target file")
    parser.add_argument("data", help="input data")
    args = parser.parse_args()

    atomic_write_text(args.file, args.data)


if __name__ == "__main__":
    main()
