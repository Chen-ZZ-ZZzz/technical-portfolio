"""Given a config file containing:
key=value

Write a function that:
updates or inserts a key
preserves other lines
writes atomically
preserves file permissions if file exists
"""


from pathlib import Path
import os, tempfile

def update_key_value(path: Path, key: str, val: str):
    newline = f"{key}={val}\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = []
    counter = 0

    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    content.append(newline)
                    counter += 1
                else:
                    content.append(line)
        if counter == 0:
            content.append(newline)

    # assert counter <= 1, f"There are more than one {key} in the file!"
    # Bad idea.
    # Assertions can be disabled with python -O.
    # Use:
    # if counter > 1:
    #     raise ValueError(...)
    # Never use assert for business logic validation.

        if counter > 1:
            raise ValueError(f"Duplicate key detected: {key}")

        mode = p.stat().st_mode

    else:
        content.append(newline)
        mode = None

    with tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8",
            dir=p.parent,
            delete=False
    ) as tmp:
        # tmp.write(content)
        # wrong here! write() expects a string
        tmp.writelines(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    tmp_path.replace(p)
    if mode is not None:        # dont simplify to if mode:
        # what if mode is really 0? It is not safe
        p.chmod(mode)
