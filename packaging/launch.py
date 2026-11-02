"""PyInstaller entry point: the same as ``python -m homebound`` (a package's ``__main__`` can't be the script)."""
import sys

from homebound.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
