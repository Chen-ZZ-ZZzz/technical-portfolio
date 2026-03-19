"""
copy_tree.py -- Recursively copy a directory without overwriting existing files

Usage:
    python3 copy_tree.py <source> <destination>

Rules:
    - Fail fast if a destination file already exists
    - Preserve permissions and timestamps
    - Skip symlinks entirely

"""

import argparse
import shutil
from pathlib import Path


def copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy src to dst, raising on conflicts and skipping symlinks."""
    src = Path(src)
    dst = Path(dst)

    if not src.is_dir():
        raise ValueError(f"Source must be a directory: {src}")

    for root, dirs, files in src.walk(follow_symlinks=False):
        rel = root.relative_to(src)
        dst_dir = dst / rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copystat(root, dst_dir, follow_symlinks=False)

        for fname in files:
            src_file = root / fname
            dst_file = dst_dir / fname

            if src_file.is_symlink():
                continue

            if dst_file.exists():
                raise FileExistsError(f"Conflict: {dst_file}")

            shutil.copy2(src_file, dst_file, follow_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description="safe recursive directory copy")
    parser.add_argument("source", help="source directory")
    parser.add_argument("destination", help="destination directory")
    args = parser.parse_args()

    copy_tree(args.source, args.destination)
    print(f"Copied {args.source} -> {args.destination}")


if __name__ == "__main__":
    main()
