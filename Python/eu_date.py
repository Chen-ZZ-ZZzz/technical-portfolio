"""Converting Dates from American- to European-Style

Say your boss emails you thousands of files with American-style dates (MM-DD-YYYY) in their names and needs them renamed to European-style dates (DD-MM-YYYY). This boring task could take all day to do by hand! Instead, write a program that does the following:

  1.  Searches all filenames in the current working directory and all subdirectories for American-style dates. Use the os.walk() function to go through the subfolders.

  2.  Uses regular expressions to identify filenames with the MM-DD-YYYY pattern in them—for example, spam12-31-1900.txt. Assume the months and days always use two digits, and that files with non-date matches don’t exist. (You won’t find files named something like 99-99-9999.txt.)

  3.  When a filename is found, renames the file with the month and day swapped to make it European-style. Use the shutil.move() function to do the renaming."""
import re


h = Path.home() / "iwish/prog/python3_boring/test/spam2"
date_pat = re.compile(r"(?P<month>\d{2})-(?P<day>\d{2})-(?P<year>\d{4})", re.A)

def swap_date(match) -> str:
    return f'{match.group("day")}-{match.group("month")}-{match.group("year")}'

for folder, _, fnames in os.walk(h):
    folder = Path(folder)

    for fn in fnames:
        newname = date_pat.sub(swap_date, fn)


        if newname != fn:
            old_path = folder / fn
            new_path = old.path.with_name(newpath)
            old_path.rename(new_path)
