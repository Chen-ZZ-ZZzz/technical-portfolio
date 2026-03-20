from pathlib import Path


"""
re_index.py — renumber or gap-insert prefixed files in a directory

Usage:
    python3 re_index.py <directory> <prefix>
    python3 re_index.py <directory> <prefix> --insert-gap <start> <width>

Examples:
    python3 re_index.py ./docs spam              # close gaps in spam001.txt, spam003.txt → spam001, spam002
    python3 re_index.py ./docs spam --insert-gap 3 2   # shift spam003 and above up by 2
"""


def renumber(prefix: str, root: Path) -> None:
    """Close gaps in numbered files e.g. spam001, spam003 → spam001, spam002."""
    prefix_len = len(prefix)
    index = 1
    for f in sorted(root.glob(f'{prefix}*')):
        if not f.is_file():
            continue
        suffix = f.stem[prefix_len:]
        if not suffix.isdigit():
            continue
        if int(suffix) != index:
            new_name = f'{prefix}{str(index).zfill(len(suffix))}{f.suffix}'
            f.rename(f.parent / new_name)
        index += 1


def insert_gap(prefix: str, root: Path, start: int, width: int) -> None:
    """Shift all numbered files >= start upward by width."""
    prefix_len = len(prefix)
    numbered = []
    for f in root.iterdir():
        if not f.is_file():
            continue
        if not f.stem.startswith(prefix):
            continue
        suffix = f.stem[prefix_len:]
        if not suffix.isdigit():
            continue
        numbered.append((int(suffix), f))

    for num, f in sorted(numbered, key=lambda x: x[0], reverse=True):
        if num < start:
            continue
        new_name = f'{prefix}{str(num + width).zfill(len(f.stem) - prefix_len)}{f.suffix}'
        f.rename(f.parent / new_name)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description='Renumber or gap-insert prefixed files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python3 re_index.py ./docs spam\n'
            '  python3 re_index.py ./docs spam --insert-gap 3 2'
        )
    )
    parser.add_argument('directory', type=Path, help='Target directory')
    parser.add_argument('prefix', help='Filename prefix e.g. spam')
    parser.add_argument('-i', '--insert-gap', nargs=2, type=int, metavar=('START', 'WIDTH'),
                        help='Insert a gap of WIDTH at START instead of closing gaps')
    args = parser.parse_args()

    if args.insert_gap:
        insert_gap(args.prefix, args.directory, *args.insert_gap)
    else:
        renumber(args.prefix, args.directory)


if __name__ == '__main__':
    main()
