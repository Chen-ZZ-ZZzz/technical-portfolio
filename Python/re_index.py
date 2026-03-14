from pathlib import Path


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
        zwidth = len(f.stem) - prefix_len
        new_name = f'{prefix}{str(num + width).zfill(zwidth)}{f.suffix}'
        f.rename(f.parent / new_name)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Renumber or gap-insert prefixed files.')
    parser.add_argument('directory', type=Path)
    parser.add_argument('prefix')
    parser.add_argument('--insert-gap', nargs=2, type=int, metavar=('START', 'WIDTH'))
    args = parser.parse_args()

    if args.insert_gap:
        insert_gap(args.prefix, args.directory, *args.insert_gap)
    else:
        renumber(args.prefix, args.directory)


if __name__ == '__main__':
    main()
