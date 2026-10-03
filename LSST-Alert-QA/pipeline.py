"""Backwards-compatible entry point: uv run pipeline.py [survey] [page_size | oid ...]"""
from rubin_qa.__main__ import main

if __name__ == "__main__":
    main()
