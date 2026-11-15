"""PyInstaller entry point for the backend sidecar."""

import multiprocessing

from videostudio.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
