# SkinForge 0.7.4 - ATS + ETS2, SCS trucks, multi-skin mods: trailer + truck skins, SCS's own trailers and Jon Ruda truck logos for SCS games

Builds paint-job mods for trailer packs that ship **template paint jobs** (Jon Ruda's
trailer pack: Great Dane gd53, Wilson hopper, livestock, Polar tanker). It reads the
pack's own definitions, so the paint jobs it writes match how the author made the
trailers. That means no MS2 vehicle XMLs and no post-build patch.

## Download

Grab `SkinForge.exe` from the [Releases](../../releases) page - one file, nothing to install.
Windows may show a SmartScreen warning because the exe isn't code-signed: *More info* -> *Run anyway*,
or build it yourself from this source (below).

## Get it running from source

**Option A, build the .exe (recommended):** right-click `build_exe.ps1` and pick
*Run with PowerShell*, or run
`powershell -ExecutionPolicy Bypass -File .\build_exe.ps1`.
It sets up a private build environment in `.venv` (your normal Python and conda are
not touched), then writes `dist\SkinForge.exe`. That single file is the app, and you
can copy or share it.

**Option B, run from source:** double-click `run_from_source.bat`. This needs
Python 3.10 or newer with `pip install "pillow>=11.2"`.

The app opens in its own window (Edge app mode). Closing the window quits it.

## Workflow

1. **Trailer pack:** click *Find in mod folder*, or *Choose .scs…* and pick the
   author's **Open Definition** .scs.
2. **Templates:** click *Templates* on a trailer. For every length, this writes
   `*_GUIDE.png` (line art on transparent, unused areas shaded) and `*_original.png`
   into the project folder. Put the guide as the top layer, paint underneath it, and
   hide the guide before you export.
3. **Paint and export** a square PNG. Transparent pixels take the paint colour picked
   in the shop. Opaque pixels are fixed colours.
4. **Drop the PNG** onto the slot (the sidebar row, the big preview, or the drop zone).
   Use *Copy artwork to checked* when two body types share one layout.
5. Fill in **Paint job** (name, price, finish gloss / metallic / both, colour) and
   **Mod** (mod name, author, unit code, output folder).
6. **Build mod.** The .scs goes straight into the output folder (by default your ATS
   mod folder). Start the game afterwards, because it only scans mods at launch.

## Truck skins

Load the truck's **Open Definition** next to the trailer pack. *Find in mod folder* picks
up every zip pack that ships template paint jobs. Any truck whose author ships
"Template" paint jobs shows up under **Trucks**, with one slot per template image (cab
length, roof and hood variant).

* Output long side is always the project texture size (4096 by default), whatever size
  the author's template is. Non-square templates keep their shape: a 4096 x 2048 (2:1)
  template gives a 4096 x 2048 skin. Paint at the template's shape; SkinForge warns if
  your image's shape doesn't match. *Templates* export upscales 2048 templates (W900) to 4096, so
  you always paint on a 4096 canvas.
* Authors ship each template twice: normal and alternate UV set. SkinForge ships the
  **normal** one by default, like Jon's own default and your MS2 truck skins. Switch
  *Paint job → Trucks: UV set* to *Normal + Alt UV* to add "(Alt UV)" entries as well.
* **Separate parts** (Pete 389: headache rack, rear bumper, rear fenders) are shared
  across the truck. Art added on one cab is reused by every cab with that part.
* **Cabs this skin goes on** (Slot tab, trucks): tick exactly which cabs get the skin.
  Default = what the author's template says. Jon's W900 "90s" templates point at cab names
  that don't exist (confirmed in game), so SkinForge corrects them: 42 · 90s -> FlatTop Old 42",
  60 · 90s -> FlatTop Old 60", 60a · 90s -> AeroDyne Old 60", Daycab90s -> DayCab Old, and
  Daycab -> the new daycabs only. A yellow dot = a template that matches no cab at all.

Tested in the app: Pete 389 Glider (30 templates) and KW W900 Highway Killer (10).

## Mirrored pieces (upright sheet)

Some templates lay pieces out upside down or mirrored. On Jon's W900 that's the whole
right side. SkinForge lets you paint those pieces the right way up:

1. Select the cab slot. In the Slot tab, under **Mirrored pieces**, click *+ Mark mirrored
   pieces*, then click each upside-down piece on the preview, one after another. Press Esc
   (or *Done marking*) when finished.
   If a click grabs too much (pieces that touch their neighbours, like the W900 38),
   **drag a box** around the piece instead: the piece is cut off at the box edge, and the
   oversized mark is replaced. Each mark gets a
   flip: vertical (default), horizontal or rotate 180°. A cyan outline shows the piece found.
2. Click **Templates**. The main sheet comes out with the marked pieces hatched blue
   (don't paint there), plus `<cab>_upright.png` with those pieces turned upright.
3. Paint the main sheet and the upright sheet, and drop the upright PNG on the
   *Drop your upright sheet* box. The **Upright** view shows it; Preview and Clean show
   the result in the game layout.
4. Build. SkinForge flips the upright pieces back into place in the texture.

**Already painted the flipped layout by hand?** *Make upright sheet from my artwork* cuts the
marked pieces out of your current artwork, turns them upright and uses that as the upright
sheet (a copy is saved as `<cab>_upright_from_art.png` in the templates folder). The build
result is pixel-identical until you change it, so you can rework those pieces the right way up.

When an upright sheet is written (Templates export or *Make upright sheet*), SkinForge locks
where the pieces sit, so adding or removing marks later never shifts a sheet you've painted.

Pieces with the same flip turn as one group, so the upright sheet looks like the real truck
side (on the W900, the tankers and stairs sit under the cab).

*Copy marks to other cabs* only adds marks: a cab's own marks are never removed or replaced.
A mark is carried over only where that piece has the same size on the other cab, so check
each cab and click the rest yourself.

## ATS or ETS2 (0.6)

The **ATS | ETS2** switch in the header picks the game of the project. Each game keeps its own
sources (mod packs + extracted game files), and the output goes to that game's mod folder.
SkinForge remembers your extracted game files per game, so new projects are ready to paint.
Skins are built the same way in both games.

## SCS's own trucks (0.6, ETS2 Scania S first)

1. Add your extracted game files (*SCS game files…*). For ETS2 that is the folder holding
   `game files\def` and `base_vehicle` (a `def` folder next to `base_vehicle` works too).
2. Under **SCS trucks** every truck is listed. Click **Load** on the one you want: SkinForge
   reads its cabs and parts once (a minute the first time on a cloud drive, then cached).
3. Slots per truck:
   * **Cab** sheets, one per cab model (Scania S: Highline, Normal, Highline 8x4) at the project
     resolution. Parts that use the cab texture (no own texture in SCS's liveries) are drawn on it.
   * **Parts** SCS gives their own texture: side skirts, rear fenders, sun visors, mirrors...
     sized like SCS's own (mirrors 256 -> 1024, skirts / fenders at SCS's shape).
4. Build: like SCS's own liveries, one paint job per cab you painted (pointed at that cab), all
   sharing the parts. Paint only parts and the paint job goes on every cab.

ATS trucks: their definitions and models are not in def.scs / base_vehicle.scs - once their
archive is extracted into the same folder they show up the same way.

## Mod trucks with painted parts + "Your logo" textures (0.7)

* Trucks whose author template covers only the cab (Jon Ruda's Torpedo NextGen) now also get
  every part the author's own skins paint separately (Torpedo: the box "buss", side skirts,
  side guards, grille mask, eyelids / ribs) under **Separate parts**. Templates come from the
  author's part templates (buss.dds, sguard.dds...) or, with the main pack loaded, from the 3D
  models. Painting only a part (e.g. only the box) builds a paint job too; the cab takes the colour.
* **Logos**: every vehicle texture named `your*.dds` in the loaded packs becomes a logo slot
  (Torpedo: Logo, Text, Number, Container / box, Mudflaps), at the author's own size. ATS
  projects also keep the built-in Jon Ruda ATS truck logos; ETS2 projects show only what the
  packs ship.
* SCS model templates: dark grey background, white parts, panel borders plus the model's inner
  edges as thin lines (seams, lights, grilles).
* Parts SCS's own liveries use but that are missing from the list come from DLC archives
  (Mighty Griffin "mg" parts, painted logo / chassis covers...): extract those DLC .scs into
  their own subfolder of your extracted game files folder.

## Taskbar icon (0.7.4)

* The SkinForge window gets its own taskbar identity (not grouped under Chrome / Edge). Pinning
  it pins SkinForge with its own icon and starts SkinForge.exe. Unpin an older pin first.

## Mod vehicles without templates (0.7.3)

* Trucks and trailers from mod packs whose author ships no paint template (SpecTransGroup
  trailer, Adwin Stam...) get templates drawn from their 3D models, like SCS's own vehicles:
  groups **Mod trailers / Mod trucks · templates from 3D models**. Works with or without the
  SCS game files loaded. Add the main pack too (if the author ships it unlocked) - the Open Definition has no models. Locked
  packs (SCS# format) are not read.
* Vehicles the game can't skin (no truckpaint materials - colours baked into the textures, e.g.
  Adwin Stam's tipper) are listed with that reason instead of silently missing.

## Truck mods that replace / add SCS trucks (0.7.1)

* Add a truck mod like Eugene's **Next Generation Scania P G R S Pack** (.zip or .scs) under
  *Choose .scs…* together with your SCS game files. Its trucks show under **SCS trucks** the way
  the game sees them: Scania P and G added, R and S with Eugene's cabs and parts.
* Packs with DLC folders (manifest.sii + base\ + dlc_xxx\) are read like the game mounts them.
* Older text .mat files (`material : "eut2.truckpaint..."`) are now recognised as paintable.
* Skins built for P/G need Eugene's pack in game; R/S skins built with his pack loaded fit his cabs.

## Several skins in one mod (0.5.1)

The **Skins in this mod** card (top of the sidebar) holds every skin of the project.

* **+ New skin**: an empty set of slots (every trailer, truck and SCS part) with its own name.
* **Duplicate**: copies the current skin's artwork and shop icon - handy for colour variants.
* Click a skin to edit it: the slots, previews, Paint job name and Mod tab shop icon switch to it.
* Each skin has its own **name, artwork and shop icon**. Finish (gloss / metallic / both),
  price, default colour and metallic settings are one setting for the whole mod.
* The build puts all skins into one .scs. Every skin + slot gets its own permanent ID, so
  units never clash. Projects from older versions open as "Skin 1" with all their IDs unchanged.
* **Delete**: a skin that was never built is removed. A skin that was built is kept in the mod
  as plain-colour paint jobs, so saved games that use it still load.
* Mirrored-piece marks and "Cabs this skin goes on" are shared by all skins; upright sheets are per skin.
* Jon Ruda logos are texture replacements, so they are shared by all skins.

**Own paint colour per template:** pick a colour with *Custom* above the preview and that
template's paint job gets it as its base colour (in the current skin). *Default* = the colour
from the Paint job tab. *None* only switches the preview to a checkerboard. SCS trailers have
one paint job per trailer, so the colour applies to the whole trailer.

## SCS's own trailers (0.5, box trailer first)

SCS ships no templates, so SkinForge makes them from the game's own 3D models.

1. Extract `def.scs` and `base_vehicle.scs` with the SCS Extractor into one folder
   (you get `<folder>\def\...` and `<folder>\base_vehicle\...`).
2. Click **SCS game files…** and pick that folder (or its `def` / `base_vehicle` subfolder).
   SkinForge remembers it for new projects. First load reads every trailer model once
   (a minute on a cloud drive); after that it comes from the cache.
3. Under **SCS trailers** every paintable part is a slot: bodies per length (dry van,
   insulated and reefer share one layout), front wall (plain and reefer fronts share one),
   wind deflector, rear doors / roll-up / aero doors, left and right skirts per length.
   Parts with the same UV layout (skirt with and without cut-out, roll-up with lift gate)
   share one texture, like SCS does.
4. **Templates** writes each part at its texture size. Body templates name the faces and
   show where the front is (RIGHT SIDE  FRONT >, < FRONT  LEFT SIDE, ROOF).
5. Paint only the parts you want; empty parts take the paint colour. The whole trailer is
   **one paint job** in the shop (gloss / metallic as set in Paint job).

Texture sizes follow SCS's own texel density (53' body 4096 x 2048, skirts 2048 x 256), with
fronts and doors raised to half the project resolution so logos stay sharp. Changing the
texture size (Paint job tab) resizes every SCS part.

Only parts with a `truckpaint` material in the model can take a texture. On tankers the tank
is part of the chassis model, so they get one *Chassis* slot per length (variants with the same
layout share it, exactly like SCS's own paint jobs: 53' 4-axle + 4-axle steer, 43' rear + hook,
28' single + double + hook, 32' + B-double). Template labels show where each face is and warn
when a face is laid out rotated (the tanker's right side is ROTATED 180° - paint it upside down).

## Jon Ruda truck logos ("Your Logo")

The sidebar has a **Jon Ruda trucks - Your Logo** group with 8 slots: logo, rear logo,
front steps and rear steps, each with a black and a white option. Every slot replaces
Jon's texture on all the trucks that use it (Pete 389 Glider, KW W900 Highway Killer,
Pete 362, Freightshaker Coronado SD, Lord G350, RAM 3500).

* Drop a logo PNG on a slot. Transparent background; non-square images are centred.
  The output is 2048 x 2048 DXT5, the same as Jon's own textures.
* **Import…** pulls all 8 logos out of an existing logo mod (for example
  *Custom logos Ruda Trucks.scs*).
* Logos go into the same .scs as your trailer skins. A project with only logos builds
  fine too.
* Load the mod **above** the Jon Ruda truck mods. In the truck shop, pick the
  *Your Logo* accessory option.

## Save-game safety

* Every slot gets a permanent ID (`<code>01`, `<code>02` …) that never changes
  between rebuilds.
* The unit code locks after the first build.
* A skin that has shipped once is **never dropped** from later builds. If you remove
  its artwork, it is kept as a plain-colour paint job. Deleting a paint job that a
  saved trailer uses crashes the game on load.

## Projects

Projects live in `Documents\SkinForge\<name>\` (`project.json` plus `art\` and
`templates\<truck or trailer>\`). An "Untitled" project folder is renamed automatically the
first time you set the paint job name; *Rename project…* in the project menu renames it any time. The settings and preview cache are in `%LOCALAPPDATA%\SkinForge`.

## Status

Paint jobs, textures (.tobj + DXT5 DDS) and the .scs packer are SkinForge's own; built skins have
been tested in game for Jon Ruda trailers and trucks and for SCS's own trailers.

| Trailer | Template slots | Notes |
|---|---|---|
| gd53 Great Dane (dry / reefer, 28/36/48/53) | 8 | structure proven |
| wilson hopper (34/43/53) | 3 | structure proven |
| livestockjr (50/53) | 2 | structure proven |
| polart Polar tanker (2 / 3 axle + fender part) | 2 | fender override untested |

Trailers and logos: confirmed in game (0.2). Truck skins (0.3): in-game test pending.
SCS base-game trailers (0.5): model-template method proven in game with the box trailer test mod; the app build is pending its first in-game test. SCS base-game trucks come next.

## Credits

* Vehicle mods SkinForge supports belong to their authors (Jon Ruda, Eugene and others) -
  skins built with SkinForge need the original mod installed. SkinForge never repackages
  or extracts their files.
* Model format reference: ConverterPIX by mwl4.
* SCS Software for Euro Truck Simulator 2 and American Truck Simulator.

## License

MIT - see `LICENSE`. Not affiliated with SCS Software.
