"""Fair Sorting - web mode.

Serves the same interface as the desktop app to any browser on your network.

    python web_sorter.py                 # http://<this-pc>:8501, PIN protected
    python web_sorter.py --port 8600     # another port
    python web_sorter.py --no-browser    # don't open a local tab
"""

import sys

from fairsort.desktop import main

if __name__ == "__main__":
    raise SystemExit(main(["--web", *sys.argv[1:]]))
