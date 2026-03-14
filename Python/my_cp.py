from pathlib import Path
import shutil

DESTINATIONS = {
    '.pdf': 'new_pdf_f',
    '.jpg': 'new_jpg_f',
}

def copy_by_extension(src_root: Path, base_dst: Path) -> None:
    dirs = {ext: (base_dst / name) for ext, name in DESTINATIONS.items()}
    for dst in dirs.values():
        dst.mkdir(parents=True, exist_ok=True)

    count = 0
    for file in src_root.rglob('*'):
        if file.is_file():
            dst = dirs.get(file.suffix.lower())
            if dst:
                shutil.copy(file, dst)
                print(f"Copied: {file.name} → {dst}")
                count += 1

    print(f"Done — {count} file(s) copied.")


if __name__ == '__main__':
    main()
