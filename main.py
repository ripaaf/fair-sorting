"""Fair Sorting - desktop app.

Run:  python main.py            (desktop window)
      python main.py --web      (serve to phones/other PCs on your Wi-Fi)
"""

from fairsort.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
