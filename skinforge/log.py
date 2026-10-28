"""SkinForge's log: one rotating text file per PC, for finding out what went wrong.

%LOCALAPPDATA%\\SkinForge\\logs\\skinforge.log (1 MB, 4 old copies kept). Everything printed to
stdout / stderr (tracebacks included) lands in it with a timestamp, plus the events the app logs
itself (sources, Load, builds, errors from the window). `report()` zips the logs with the
settings and the open project (no artwork) into one file a user can send."""
from __future__ import annotations

import io
import os
import platform
import sys
import threading
import time
import traceback
import zipfile

MAX_BYTES = 1024 * 1024
KEEP = 4
_lock = threading.RLock()
_fh = None
_path = None


def log_dir() -> str:
    from .platform_util import app_data_dir
    d = os.path.join(app_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def log_path() -> str:
    return _path or os.path.join(log_dir(), "skinforge.log")


def _rotate():
    global _fh
    try:
        if _fh is None or _fh.tell() < MAX_BYTES:
            return
        _fh.close()
        base = log_path()
        for i in range(KEEP - 1, 0, -1):
            a, b = f"{base}.{i}", f"{base}.{i + 1}"
            if os.path.exists(a):
                os.replace(a, b)
        os.replace(base, base + ".1")
        _fh = open(base, "a", encoding="utf-8", buffering=1)
    except OSError:
        pass


def write(level: str, msg: str):
    """One log entry; multi-line text (tracebacks) is indented under its timestamp."""
    with _lock:
        if _fh is None:
            return
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            lines = str(msg).rstrip("\n").split("\n")
            _fh.write(f"{stamp} {level:5} {lines[0]}\n" + "".join(f"{'':26}{x}\n" for x in lines[1:]))
            _rotate()
        except (OSError, ValueError):
            pass


def info(msg):
    write("INFO", msg)


def warn(msg):
    write("WARN", msg)


def error(msg, exc: BaseException | None = None):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
    write("ERROR", f"{msg}\n{tb}" if tb else msg)


class _Tee(io.TextIOBase):
    """stdout / stderr -> the console (when there is one) + the log, line by line."""

    def __init__(self, stream, level):
        self.stream, self.level, self.buf = stream, level, ""

    def writable(self):
        return True

    def write(self, s):
        if self.stream is not None:
            try:
                self.stream.write(s)
            except (OSError, ValueError):
                pass
        with _lock:
            self.buf += s
            if "\n" in self.buf:
                text, self.buf = self.buf.rsplit("\n", 1)
                # a traceback arrives in many writes: keep it in one entry
                write(self.level, text)
        return len(s)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except (OSError, ValueError):
                pass


def setup():
    """Open the log and route stdout / stderr / uncaught exceptions into it. Safe to call twice."""
    global _fh, _path
    if _fh is not None:
        return log_path()
    try:
        _path = os.path.join(log_dir(), "skinforge.log")
        _fh = open(_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        _fh = None
        return None
    sys.stdout = _Tee(sys.stdout, "OUT")
    sys.stderr = _Tee(sys.stderr, "ERR")

    def hook(t, v, tb):
        write("ERROR", "Uncaught: " + "".join(traceback.format_exception(t, v, tb)))
    sys.excepthook = hook
    threading.excepthook = lambda a: write(
        "ERROR", f"Uncaught in thread {a.thread.name if a.thread else '?'}: "
                 + "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    from . import __version__
    info("=" * 60)
    info(f"SkinForge {__version__} starting · Python {platform.python_version()} · {platform.platform()}"
         f" · {'exe' if getattr(sys, 'frozen', False) else 'source'}")
    return _path


def tail(n: int = 400) -> str:
    try:
        with open(log_path(), "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def report(out_dir: str, app=None) -> str:
    """SkinForge_report_<time>.zip: logs + settings + the open project file + what's loaded.
    No artwork, so it stays small and private."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, time.strftime("SkinForge_report_%Y%m%d_%H%M%S.zip"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        base = log_path()
        for i, fp in enumerate([base] + [f"{base}.{k}" for k in range(1, KEEP + 1)]):
            if os.path.isfile(fp):
                z.write(fp, os.path.basename(fp))
        if app is not None:
            try:
                if os.path.isfile(app.settings_path):
                    z.write(app.settings_path, "settings.json")
                if app.project_file and os.path.isfile(app.project_file):
                    z.write(app.project_file, "project/" + os.path.basename(app.project_file))
                info_ = {
                    "sources": app.source_status,
                    "vehicles": [{"id": t.id, "name": t.name, "kind": getattr(t, "vkind", "") or
                                  (t.slots[0].kind if t.slots else ""), "slots": len(t.slots),
                                  "lazy": getattr(t, "lazy", False), "error": getattr(t, "error", "")}
                                 for t in app.trailers],
                    "last_build": app.build_state,
                }
                z.writestr("state.json", json.dumps(info_, indent=2, default=str))
            except Exception as e:      # the report must never fail because of one part
                z.writestr("report_error.txt", f"{type(e).__name__}: {e}")
    info(f"Error report saved: {out}")
    return out
