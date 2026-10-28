"""Windows-aware paths, folder opening, native dialogs and the app window."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def documents_dir() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (follows OneDrive redirection)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def game_mod_dirs() -> dict:
    d = documents_dir()
    out = {}
    for game, folder in (("ATS", "American Truck Simulator"), ("ETS2", "Euro Truck Simulator 2")):
        p = os.path.join(d, folder, "mod")
        if os.path.isdir(p):
            out[game] = p
    return out


def app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    p = os.path.join(base, "SkinForge")
    os.makedirs(p, exist_ok=True)
    return p


def projects_dir() -> str:
    p = os.path.join(documents_dir(), "SkinForge")
    os.makedirs(p, exist_ok=True)
    return p


def open_folder(path: str, select: bool = False):
    if not os.path.exists(path):
        return
    if sys.platform == "win32":
        if select and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path if os.path.isdir(path) else os.path.dirname(path))
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


_dialog_lock = __import__("threading").Lock()


def native_dialog(kind: str, title: str = "", initial: str = "", filetypes=None):
    """kind: open | dir | save. Returns a path or '' (cancel) or None (no dialog support)."""
    with _dialog_lock:
        try:
            return _native_dialog(kind, title, initial, filetypes)
        except Exception:
            return None


def _native_dialog(kind, title, initial, filetypes):
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return None
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        kw = {"title": title or "SkinForge", "parent": root}
        if initial:
            kw["initialdir"] = initial if os.path.isdir(initial) else os.path.dirname(initial)
        if kind == "dir":
            r = filedialog.askdirectory(**kw)
        elif kind == "save":
            r = filedialog.asksaveasfilename(filetypes=filetypes or [("All files", "*.*")], **kw)
        else:
            r = filedialog.askopenfilename(filetypes=filetypes or [("All files", "*.*")], **kw)
        return os.path.normpath(r) if r else ""
    finally:
        root.destroy()


def find_browser_for_app_window():
    """Edge (always present on Windows 10/11) or Chrome, for a chromeless --app window."""
    cands = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            w = shutil.which(n)
            if w:
                cands.append(w)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


APP_ID = "SkinForge.App"


def _relaunch():
    """(command, icon resource) Windows uses for a pinned SkinForge window."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}"', f"{exe},0"
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "skinforge.ico")
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    cmd = f'"{pyw if os.path.isfile(pyw) else py}" "{script}"' if script.endswith(".py") \
        else f'"{pyw if os.path.isfile(pyw) else py}" -m skinforge'
    return cmd, f"{ico},0"


def brand_app_window(pid=None, timeout: float = 30.0):
    """Give the browser app window SkinForge's own taskbar identity (Windows): own AppUserModelID,
    icon and relaunch command, so it isn't grouped under Chrome / Edge and pinning it pins
    SkinForge. Runs in a background thread; any failure just leaves the browser's icon."""
    if sys.platform != "win32":
        return
    import threading
    threading.Thread(target=_brand_worker, args=(pid, timeout), daemon=True).start()


def _brand_worker(pid, timeout):
    import ctypes
    import time
    from ctypes import wintypes as W
    try:
        u32, sh32, ole = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.ole32
        u32.LoadImageW.restype = W.HANDLE
        u32.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
        u32.SendMessageW.restype = ctypes.c_ssize_t
        u32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u32.IsWindowVisible.argtypes = [W.HWND]
        u32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        sh32.SHGetPropertyStoreForWindow.argtypes = [W.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        try:
            sh32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
        except Exception:
            pass

        class GUID(ctypes.Structure):
            _fields_ = [("d1", W.DWORD), ("d2", W.WORD), ("d3", W.WORD), ("d4", ctypes.c_ubyte * 8)]

        def guid(s):
            import uuid
            b = uuid.UUID(s).bytes_le
            g = GUID()
            ctypes.memmove(ctypes.byref(g), b, 16)
            return g

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", W.DWORD)]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [("vt", W.USHORT), ("r1", W.WORD), ("r2", W.WORD), ("r3", W.WORD),
                        ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

        FMT = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
        IID_IPropertyStore = guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
        cmd, icon = _relaunch()
        props = [(2, cmd), (3, icon), (4, "SkinForge"), (5, APP_ID)]   # relaunch cmd/icon/name, then ID

        found = []
        EnumProc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

        def cb(hwnd, _):
            if not u32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, buf, 256)
            if not buf.value.startswith("Chrome_WidgetWin"):
                return True
            u32.GetWindowTextW(hwnd, buf, 256)
            if not buf.value.startswith("SkinForge"):
                return True
            found.append(hwnd)
            return True

        cbp = EnumProc(cb)
        end = time.time() + timeout
        done = set()
        ole.CoInitialize(None)
        hicon_big = hicon_small = None
        ico = icon.rsplit(",", 1)[0]
        try:
            hicon_big = u32.LoadImageW(None, ico, 1, 48, 48, 0x10) if ico.lower().endswith(".ico") else None
            hicon_small = u32.LoadImageW(None, ico, 1, 16, 16, 0x10) if ico.lower().endswith(".ico") else None
            if not hicon_big:
                big, small = W.HICON(), W.HICON()
                sh32.ExtractIconExW(ico, 0, ctypes.byref(big), ctypes.byref(small), 1)
                hicon_big, hicon_small = big.value, small.value
        except Exception:
            pass
        while time.time() < end:
            found.clear()
            u32.EnumWindows(cbp, 0)
            for hwnd in found:
                if hwnd in done:
                    continue
                store = ctypes.c_void_p()
                if sh32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(IID_IPropertyStore),
                                                    ctypes.byref(store)) != 0 or not store.value:
                    continue
                vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0],
                                   ctypes.POINTER(ctypes.c_void_p))
                SetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                              ctypes.POINTER(PROPVARIANT))(vtbl[6])
                Commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
                Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
                keep = []
                for pid_, val in props:
                    k = PROPERTYKEY(guid(FMT), pid_)
                    sbuf = ctypes.create_unicode_buffer(val)
                    keep.append(sbuf)
                    pv = PROPVARIANT(31, 0, 0, 0, ctypes.cast(sbuf, ctypes.c_void_p), None)  # VT_LPWSTR
                    SetValue(store, ctypes.byref(k), ctypes.byref(pv))
                Commit(store)
                Release(store)
                if hicon_big:
                    u32.SendMessageW(hwnd, 0x0080, 1, hicon_big)                 # WM_SETICON big
                    u32.SendMessageW(hwnd, 0x0080, 0, hicon_small or hicon_big)  # small
                done.add(hwnd)
            if done:
                time.sleep(2.0)       # catch a window Chrome recreates right after start, then stop
                found.clear()
                u32.EnumWindows(cbp, 0)
                if all(h in done for h in found):
                    return
            time.sleep(0.3)
    except Exception:
        import traceback
        traceback.print_exc()


def launch_app_window(url: str):
    """Start a dedicated browser profile in app mode. Returns the Popen or None."""
    exe = find_browser_for_app_window()
    if not exe:
        return None
    profile = os.path.join(app_data_dir(), "window")
    args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-features=Translate", "--window-size=1600,980",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding"]
    try:
        proc = subprocess.Popen(args)
    except OSError:
        return None
    brand_app_window(proc.pid)
    return proc
