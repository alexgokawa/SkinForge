import argparse
import sys


def main():
    ap = argparse.ArgumentParser(prog="SkinForge")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true", help="run the server only (open the URL yourself)")
    a = ap.parse_args()
    if sys.stdout is None or sys.stderr is None:   # windowed .exe: no console
        from .platform_util import app_data_dir
        import os
        log = open(os.path.join(app_data_dir(), "skinforge.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    from .server import serve
    serve(port=a.port, open_window=not a.no_window)


if __name__ == "__main__":
    sys.exit(main())
