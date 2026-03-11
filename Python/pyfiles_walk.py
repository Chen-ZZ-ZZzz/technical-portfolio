from pathlib import Path

SKIP = frozenset({".git", ".venv", "__pycache__"})

def iter_python_files(base: Path):
    """Yield absolute .py file paths, skipping common non-project dirs."""
    for root, dirs, files in Path(base).walk(top_down=True):
        dirs[:] = [d for d in dirs if d not in SKIP]
        yield from (root / f for f in files if (root / f).suffix == ".py")


if __name__ == '__main__':
    for p in iter_python_files(Path.home()):
        print(p)
