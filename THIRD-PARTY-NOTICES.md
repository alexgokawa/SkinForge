# Third-party notices

SkinForge's own source code is released under the MIT License (see `LICENSE`).

## What the SkinForge.exe contains

`SkinForge.exe` is a single-file build made with PyInstaller (`build_exe.ps1`). Besides
SkinForge's code, it bundles the following open-source components. Each one keeps its own
license.

| Component | Used for | License |
| --- | --- | --- |
| [Python](https://www.python.org) (runtime, standard library, tkinter with Tcl/Tk) | Runs the app, file dialogs | PSF License; Tcl/Tk uses a BSD-style license |
| [Pillow](https://python-pillow.org) | Image loading, resizing and drawing | MIT-CMU (HPND) |
| [NumPy](https://numpy.org) | Texture and pixel math | BSD-3-Clause (the wheels also include a few other permissively licensed libraries, listed in NumPy's own license file) |
| [PyInstaller](https://pyinstaller.org) | Packs the app into one exe | GPL-2.0-or-later with a special exception that lets programs built with it be distributed under any license |

The exact versions are set by `requirements.txt` and `build_exe.ps1`. When you build the exe
yourself, each package's license file is in the `.venv` folder that `build_exe.ps1` creates.

## Not included

SkinForge does not bundle or redistribute any game files or any other mod author's work. It reads
the game's own `.scs` archives and the mods you have installed on your computer, and builds new
paint-job mods from your artwork.

- Euro Truck Simulator 2, American Truck Simulator and their files belong to SCS Software.
- Vehicle mods that SkinForge supports (for example Jon Ruda's trailer and truck packs, and
  Eugene's Scania pack) belong to their authors. Skins built with SkinForge need the original
  mod installed.
- Community template sets mentioned in the README belong to their authors and are not included.

## Credits

- Model format reference: ConverterPIX by mwl4. No ConverterPIX code is included.

SkinForge is not affiliated with or endorsed by SCS Software.
