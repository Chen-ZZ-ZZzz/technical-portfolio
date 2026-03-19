"""
config_update.py -- Update or insert a key=value pair in a config file atomically

Usage:
    python3 config_update.py <file> <key> <value>

"""

import argparse
import os
import tempfile
from pathlib import Path


def update_key_value(path: Path, key: str, val: str) -> None:
    """Update or insert a key=value pair, preserving other lines and permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    newline = f"{key}={val}\n"
    content = []
    found = 0
    mode = None

    if path.exists():
        mode = path.stat().st_mode

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    content.append(newline)
                    found += 1
                else:
                    content.append(line)

        if found > 1:
            raise ValueError(f"Duplicate key detected: {key}")

        if found == 0:
            content.append(newline)
    else:
        content.append(newline)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        delete=False, dir=path.parent
    ) as tmp:
        tmp.writelines(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    if mode is not None:
        path.chmod(mode)

    dir_fd = os.open(path.parent, os.O_DIRECTORY)
    os.fsync(dir_fd)
    os.close(dir_fd)


def main():
    parser = argparse.ArgumentParser(description="update key=value in a config file")
    parser.add_argument("file", help="path to the config file")
    parser.add_argument("key", help="config key")
    parser.add_argument("value", help="config value")
    args = parser.parse_args()

    update_key_value(args.file, args.key, args.value)
    print(f"Updated {args.key}={args.value} in {args.file}")


if __name__ == "__main__":
    main()
