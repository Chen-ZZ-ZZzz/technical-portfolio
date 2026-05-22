#!/usr/bin/env python3
"""
mass-camera-tidy –– mass cleanup for camera dumps.

Walks DIR non-recursively, then in order of priority:
  1. Date rename  : insert _YYMM_ into undated photo/video filenames
  2. chmod -x     : strip execute bit (a-x) from all whitelisted files
  3. Lowercase ext: .JPG -> .jpg, .MP4 -> .mp4, etc.
  4. Rotate JPGs  : apply EXIF orientation losslessly (via exiftran)

Pre-flight runs first: filename conflicts and unreadable metadata are
collected. On any error, nothing is changed and the script exits non-zero.
On clean pre-flight, all four steps run.

Date source:
  --date YYMM (overrides metadata for the whole batch), else
  exiftool's DateTimeOriginal / CreateDate / MediaCreateDate.
  No mtime fallback.

Detection rules for "already has a date" (skipped silently). A stem
matching any of these patterns is considered dated:
  - canonical  _YYMM_   anywhere in stem            IMG_2207_13131.jpg
  - leading    YYMM_    at start of stem            2207_IMG_13131.jpg
  - legacy separated:   _NNNN+_YYMM at end          IMG_13131_2207.jpg
  - legacy embedded:    _NNNN+YYMM at end           IMG_131312207.jpg
  - YYYYMMDD anywhere   (Pixel / Android timestamp) PXL_20260516_xxx.jpg

False positives are possible (e.g. DSC_1208_2.jpg matches canonical
_YYMM_ but its 1208 is really a seq). Those skip the date rename but still
get chmod / ext / rotate. Spot and rename manually if needed.

Whitelist:
  photos: jpg jpeg heic heif cr2 cr3 arw nef dng raw rw2 orf raf
  videos: mp4 mov avi mts m2ts mkv
  everything else (xmp sidecars, dotfiles, subdirs) is ignored silently.

Usage:
  mass-camera-tidy [DIR] [--date YYMM]

Dependencies: exiftool (required unless --date is given), exiftran (for
the orientation step; warns and skips if missing).
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".cr2", ".cr3", ".arw",
              ".nef", ".dng", ".raw", ".rw2", ".orf", ".raf"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mts", ".m2ts", ".mkv"}
ALL_EXTS = PHOTO_EXTS | VIDEO_EXTS
JPEG_EXTS = {".jpg", ".jpeg"}
MONTH = r"(?:0[1-9]|1[0-2])"    # valid month component: 01-12
DAY   = r"(?:0[1-9]|[12]\d|3[01])"  # valid day component: 01-31


def _is_dated(stem):
    """
    True if stem already has a date embedded. Each pattern matches
    one canonical or legacy form; any hit means "skip date rename".
    """
    patterns = [
        rf"_\d{{2}}{MONTH}_",               # canonical    _YYMM_
        rf"^\d{{2}}{MONTH}_",               # leading      YYMM_
        rf"_\d{{4,}}_\d{{2}}{MONTH}$",      # legacy sep   _NNNN+_YYMM$
        rf"_\d{{6,}}{MONTH}$",              # legacy emb   _NNNN+YYMM$
        rf"\d{{4}}{MONTH}{DAY}",            # YYYYMMDD     Pixel / Android
    ]
    return any(re.search(p, stem) for p in patterns)


def _add_date_to_stem(stem, yymm):
    """
    Insert _YYMM_ after a leading letter prefix.
    If the stem has no letters, prepend YYMM_.
      IMG_13131  + 2207 -> IMG_2207_13131
      DSC9999    + 2207 -> DSC_2207_9999
      12345      + 2207 -> 2207_12345
    """
    m = re.match(r"^([A-Za-z]+)_?(\d.*)$", stem)
    if m:
        return f"{m.group(1)}_{yymm}_{m.group(2)}"
    return f"{yymm}_{stem}"


def _read_date_yymm(path):
    """Return YYMM (4 chars) from EXIF/container metadata, or None."""
    try:
        out = subprocess.check_output(
            ["exiftool", "-s3",
             "-d", "%y%m",
             "-DateTimeOriginal",
             "-CreateDate",
             "-MediaCreateDate",
             str(path)],
            stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            line = line.strip()
            if re.fullmatch(rf"\d{{2}}{MONTH}", line):
                return line
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def _get_orientation(path):
    """Return EXIF Orientation value (1-8) or 1 if unknown."""
    try:
        out = subprocess.check_output(
            ["exiftool", "-s3", "-n", "-Orientation", str(path)],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return int(out) if out.isdigit() else 1
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 1


def _parse_args():
    parser = argparse.ArgumentParser(description=
        "Mass cleanup for camera dumps: date rename, chmod -x, lowercase ext, rotate JPGs.")
    parser.add_argument("directory", nargs="?", default=".",
                    help="Directory to process (default: current).")
    parser.add_argument("--date", "-d", metavar="YYMM",
                    help="Force this YYMM for all undated files; "
                         "overrides metadata. Must be 4 digits, month 01-12.")
    args = parser.parse_args()

    if args.date is not None:
        if not re.fullmatch(rf"\d{{2}}{MONTH}", args.date):
            parser.error("--date must be 4 digits with valid month 01-12 (e.g. 2207)")
    return args


def main():
    args = _parse_args()
    directory = Path(args.directory)
    if not directory.is_dir():
        sys.exit(f"error: not a directory: {directory}")

    forced_yymm = args.date

    if not forced_yymm and not shutil.which("exiftool"):
        sys.exit("error: exiftool not found. Install it or pass --date YYMM.")

    # walk: non-recursive, whitelisted extensions, skip hidden files
    files = sorted(
        p for p in directory.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in ALL_EXTS
    )

    # track planned final names to detect collisions against existing files and planed new names.
    virtual_names = {p.name for p in files}

    errors = []
    # list of (src_path, final_name)
    # single combined rename todo list for execute phase
    plan = []

    for p in files:
        stem = p.stem
        suffix = p.suffix

        # --- date phase ---
        if _is_dated(stem):
            new_stem = stem
        else:
            if forced_yymm:
                yymm = forced_yymm
            else:
                yymm = _read_date_yymm(p)
                if not yymm:
                    errors.append(
                        f"no date metadata: {p.name} "
                        f"(pass --date YYMM to override)"
                    )
                    continue
            new_stem = _add_date_to_stem(stem, yymm)

        # --- ext phase ---
        new_suffix = suffix.lower()
        new_name = new_stem + new_suffix

        if new_name == p.name:
            continue  # nothing to rename

        # --- collision check ---
        if new_name in virtual_names:
            errors.append(
                f"target exists: {p.name} -> {new_name}"
            )
            continue

        virtual_names.discard(p.name)
        virtual_names.add(new_name)
        plan.append((p, new_name))

    # --- errors block; nothing else to report ---
    if errors:
        for e in errors:
            print(f"error: {e}")
        print(f"\n{len(errors)} error(s). Nothing changed.")
        sys.exit(1)

    # --- apply ---

    #1. chmod a-x
    chmod_count = 0
    for p in files:
        mode = p.stat().st_mode
        new_mode = mode & ~0o111
        if new_mode != mode:
            p.chmod(new_mode)
            chmod_count += 1

    #2. rotate JPGs based on EXIF orientation (lossless)
    rotate_count = 0
    if shutil.which("exiftran"):
        for p in files:
            if p.suffix.lower() not in JPEG_EXTS:
                continue
            if _get_orientation(p) == 1:
                continue
            try:
                subprocess.check_call(
                    ["exiftran", "-ai", str(p)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                rotate_count += 1
            except subprocess.CalledProcessError:
                print(f"warning: exiftran failed on {p.name}",)
    else:
        print("warning: exiftran not installed. Skipping orientation step")

    #3. renames (combined date + ext lowercase)
    rename_count = 0
    for src, new_name in plan:
        dst = src.with_name(new_name)
        src.rename(dst)
        rename_count += 1

    print(f"DONE: {rename_count} renamed, {chmod_count} chmodded, {rotate_count} rotated")


if __name__ == "__main__":
    main()
