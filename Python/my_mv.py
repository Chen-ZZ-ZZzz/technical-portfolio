from pathlib import Path
import os, shutil

"""Write a program that walks through a folder tree and searches for 
files with a certain file extension (such as .pdf or .jpg). Copy these 
files from their current location to a new folder."""

h = Path.home() / "iwish/prog/python3_boring/test"
src_root = h / "spam"

destinations = {
".pdf": h / "new_pdf_f",
".jpg": h / "new_jpg_f",
}
for dst in destinations.values():
    dst.mkdir(exist_ok=True)

for file in src_root.rglob("*"):
    if file.is_file():
        dst = destinations.get(file.suffix.lower())
        if dst:
            print(f"{file} copies")
            shutil.copy(file, dst)

print("Done")
