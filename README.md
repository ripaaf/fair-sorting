# fair-sorting

A manual media sorter with a modern GUI — supports images, videos, and audio. Load a folder, set destinations, and sort your files with keyboard shortcuts. Includes move logging, undo, and a built-in web interface.

<img src=image/image-dark.png>

> [!NOTE]
> This app is still in pre-release. If you encounter any errors or bugs, please report them!



## Download

Head to the [releases page](https://github.com/ripaaf/fair-sorting/releases/) and pick whichever version suits you:

| Release | What it is | How to use |
| --- | --- | --- |
| `fair-sorting.exe` | Single portable executable | Download and double-click, nothing else needed |
| `fair-sorting-app.zip` | Self-contained app folder | Extract the zip, open the folder, run `fair sorting.exe` inside |

Both versions include ffmpeg and require no installation. The `.exe` is the simplest option if you just want to run it quickly. The `.zip` is the same thing but kept as an app folder — useful if you want to keep it organized somewhere.



## How to Use

1. **Load a Folder** — Select a folder containing media files (`L`)
2. **Add Destinations** — Choose one or more destination folders (`A`)
3. **Sort Files** — Use number keys `1–0` to move the current file to a destination
4. **Undo** — Press `Ctrl+Z` to undo the last move anytime



## Viewer Controls

The floating viewer control panel can be repositioned and resized:

- **Position** — Cycle between Center, Left, and Right using `C` or the Position button
- **Resize** — Drag the handle at the outer edge of the panel
- **Hide/Show** — Press `H` to collapse or expand the controls
- **Immersive Mode** — Press `F` to hide all UI and focus on the preview
- **Night Mode** — Press `T` to toggle between light and dark theme



## Keybindings

| Key | Action |
| --- | --- |
| `L` | Load source folder |
| `A` | Add destination folder |
| `1` – `0` | Move file to destination 1–10 |
| `P` / `Space` | Play or pause current media |
| `S` | Stop playback and return to start |
| `O` | Open file in external player |
| `← / →` | Previous or next file |
| `+` / `-` | Zoom in or out |
| `Ctrl+0` | Reset zoom to 100% |
| `Ctrl+Z` | Undo last move |
| `H` | Show or hide viewer controls |
| `F` | Toggle immersive mode |
| `T` | Toggle night / light mode |
| `C` | Cycle viewer control position |
| `Esc` | Exit immersive mode or show controls |



## Logfile

A move log is automatically created inside the **folder currently being sorted** each time you load a new folder. The log is named `move_log_` followed by a timestamp (e.g. `move_log_20250427123000.txt`).

- **Select Logfile** — Load any `.txt` logfile manually; new moves will be appended to it
- **Use Last** — Switch to the most recently created logfile
- **Undo from Log** — Select any entry in the log list and click **Undo Selected** to restore that file
- **Show Detail** — Right-click or use the button to preview the moved file and see its source and destination paths



## Web Interface

The app includes a built-in web server powered by Streamlit, accessible from other devices on the same network.

**To start:**
1. Click **Run Web** in the top bar
2. Enter a port (default: `8501`)
3. Click **Open** to launch in browser, or access from another device at `http://YOUR-IP:8501`

**Features:**
- Clean web UI for sorting media
- Preview images, video, and audio
- Move files to destination folders
- Undo last move
- Undo from logfile

> [!NOTE]
> The web interface requires Python and Streamlit installed on the host machine. If Streamlit is not found, the app will attempt to set up a virtual environment automatically.



## Supported File Formats

### Image
`.jpg` `.jpeg` `.png` `.gif` `.bmp` `.tiff` `.tif` `.webp` `.heic` `.heif` `.avif` `.jp2` `.j2k` `.jpf` `.jfif` `.ico` `.tga` `.ppm` `.pgm` `.pbm` `.pnm` `.hdr` `.exr` `.psd` `.raw` `.cr2` `.nef` `.orf` `.arw` `.rw2` `.dng` `.xcf` `.pcx`

### Video
`.mp4` `.avi` `.mkv` `.webm` `.mov` `.flv` `.3gp` `.wmv` `.rmvb` `.m4v` `.mpeg` `.mpg` `.divx` `.ogv` `.ts` `.m2ts` `.vob` `.mts` `.asf` `.rm` `.ogm` `.mxf` `.f4v`

### Audio
`.mp3` `.wav` `.m4a` `.ogg` `.wma` `.aac` `.ape` `.alac` `.mid` `.ac3` `.amr` `.ra` `.opus` `.flac`

_(some formats not fully tested yet)_



## Generated Files

| File / Folder | Location | Notes |
| --- | --- | --- |
| `fair_config.txt` | Same folder as `.exe` | Saves last folder and destinations |
| `move_log_*.txt` | Inside the folder being sorted | Auto-created each session |
| `output/desktop_preview_cache/` | Same folder as `.exe` | Desktop app preview cache — auto-deleted on close |
| `output/web_previews/` | Same folder as `.exe` | Web interface preview cache — **not** auto-deleted |

> [!WARNING]
> The `output/web_previews/` folder is used by the web interface to cache video and image previews. Unlike the desktop preview cache, this one is **not automatically cleared** when the app closes. If you use the web interface frequently or sort large batches of files, this folder can grow large over time and eat up disk space. It is safe to delete the contents of `output/web_previews/` manually at any time — the app will regenerate previews as needed.



# Enjoy sorting your files with **Fair Sorting**! 🚀

<img src=image/cutecom.png>
