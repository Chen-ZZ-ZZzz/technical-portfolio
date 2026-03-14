from pathlib import Path

BIG = 100 * 1024 * 1024  # 100 MB


def dir_size(path: Path) -> int:
    """Return total size in bytes of all files under path."""
    total = 0
    for entry in path.rglob('*'):
        if not entry.is_file():
            continue
        try:
            total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def find_large(root: Path, threshold: int = BIG) -> None:
    for f in root.iterdir():
        try:
            if f.is_file():
                size = f.stat(follow_symlinks=False).st_size
            elif f.is_dir():
                size = dir_size(f)
            else:
                continue
        except OSError:
            continue
        if size > threshold:
            print(f"{size / 1024 / 1024:.1f} MB  {f}")
    print('Done')


def main() -> None:
    find_large(Path.home() / 'iwish/downloads/films')


if __name__ == '__main__':
    main()
