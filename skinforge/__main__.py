import argparse
import sys


def main():
    ap = argparse.ArgumentParser(prog="SkinForge")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true", help="run the server only (open the URL yourself)")
    a = ap.parse_args()
    from . import log
    log.setup()    # %LOCALAPPDATA%\SkinForge\logs\skinforge.log (also covers the windowed .exe)
    from .server import serve
    serve(port=a.port, open_window=not a.no_window)


if __name__ == "__main__":
    sys.exit(main())
