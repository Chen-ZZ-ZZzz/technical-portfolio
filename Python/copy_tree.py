"""
Level 4 — Copying & safety semantics
Exercise 7: safe recursive copy

Write a function:
def copy_tree(src: Path, dst: Path):
    ...

Rules:
do not overwrite existing files
fail fast if a conflict exists
preserve permissions and timestamps
skip symlinks entirely

You may use shutil, but must control behavior explicitly."""

from pathlib import Path
import shutil

def copy_tree(src: Path, dst: Path):
    src = Path(src)
    dst = Path(dst)

    if not src.is_dir():
        raise ValueError("Source must be a directory")

    for root, dirs, files in src.walk(follow_symlinks=False):
        root = Path(root)

        # if folder != src:
        #     dst = dst / folder.parts[-1] # bad idea
        # Compute relative path safely
        rel = root.relative_to(src)
        root_dst = dst / rel
        root_dst.mkdir(parents=True, exist_ok=True)
        shutil.copystat(root, root_dst, follow_symlinks=False)

        for f in files:
            f = root / f
            f_dst = root_dst / f

            if f.is_smylink():
                continue        # skip symlinks entirely

            if f_dst.exists():
                # print(f"File already exists: {f.name}")
                # break
                raise FileExistsError(f"Conflict: {f_dst}")

            shutil.copy2(f, f_dst, follow_symlinks=False) # preserve metadata

from pathlib import Path
import shutil
import tempfile

def copy_tree_atomic(src: Path, dst: Path):
    src = Path(src)
    dst = Path(dst)

    if not src.is_dir():
        raise ValueError("Source must be a directory")

    if dst.exists():
        raise FileExistsError(f"Destination already exists: {dst}")

    tmp_dir = Path(tempfile.mkdtemp(dir=dst.parent))

    try:
        # Copy into temp directory
        shutil.copytree(
            src,
            tmp_dir,
            symlinks=False,
            copy_function=shutil.copy2,
            dirs_exist_ok=True,
        )

        # Atomic rename
        tmp_dir.replace(dst)

    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
