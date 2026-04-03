#!/usr/bin/env bash

set -euo pipefail
if [[ "${TRACE-0}" == "1" ]]; then
    set -o xtrace
fi

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    echo "Usage $0 /path/to/md_file

Convert a Markdown file to a PDF file

"
    exit 0
fi


main (){
    if [[ -z "${1-}" ]]; then
        echo "Usage $0 /path/to/md_file" >&2
        exit 1
    fi

    MD_FILE=$1
    EXT=${MD_FILE##*.}
    BASENAME=${MD_FILE%.*}
    PDF_FILE=$BASENAME.pdf

    if [[ ! -f "$MD_FILE" ]]; then
        echo "File does not exist: $MD_FILE" >&2
        exit 1
    fi

    if [[ "$EXT" != "md" && "$EXT" != "markdown" ]]; then
       echo "File is not markdown" >&2
       exit 1
    fi

    echo "Converting $MD_FILE to $PDF_FILE ..."
    pandoc "$MD_FILE" -o "$PDF_FILE" -V 'geometry:margin=2.5cm' -V 'fontsize=11pt'
}

main "$@"
