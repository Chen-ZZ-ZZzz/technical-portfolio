"""
leer_dir.py -- Find directories that contain no files and no subdirectories.
               Return a list of path objects sorted lexicographically.
"""

from pathlib import Path
import argparse


def handle_error(error):
    print(f"No permission to {error.filename}")


def empty_dir(base: Path):
    """Yield empty directory paths, skipping directory without permission."""
    for root, dirs, files in Path(base).walk(on_error=handle_error):
        if not dirs and not files:
            yield root


def main():
    parser = argparse.ArgumentParser(description="Find empty directories")
    parser.add_argument("base", help="folder to be checked")
    args = parser.parse_args()

    for d in sorted(empty_dir(Path(args.base)), key=str):
        print(d)


if __name__ == "__main__":
    main()
    
