# fair-sorting
a manual photo sorter using a GUI, complete with log moving and can set the destination.

the app itself you can see by the screenshot, it can sort your photo based where do you want it to put into the folder. after you open the app you can choose what folder to load and it automaticly detects all the photo, video, or audio extension from the supported file format in [here](#supported-file-formats).

<img src=screenshot.png>

# how to use
to use, you can download the [release](https://github.com/ripaaf/fair-sorting/releases/) app from the releases and you can run the exe file. the app UI/UX itself is just basic windows program so maybe it is to plain or either its hard to understand but after you use it for a while you can grip a hand to it.

1. **Load a Folder**: Select a folder containing media files.
2. **Select Destination**: Select a multiple folder for each destination your file want to place.
3. **Sort Files**: Use the keybindings to organize files into different folders.

> [!NOTE]  
> This app is still in pre-release, so if you encounter any errors or bugs, please report them!


# Supported File Formats

The Supported file format for the app are these below.

### Image Formats:
`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.psd`, `.svg`, `.raw`, `.heic`, `.cr2`, `.nef`, `.orf`, `.arw`, `.rw2`, `.dng`, `.xcf`, `.pcx`

### Video Formats:
`.mp4`, `.avi`, `.mkv`, `.webm`, `.mov`, `.flv`, `.3gp`, `.wmv`, `.rmvb`, `.m4v`, `.mpeg`, `.divx`, `.ogv`, `.ts`, `.m2ts`, `.vob`, `.mts`

### Audio Formats:
`.flac`, `.mp3`, `.wav`, `.m4a`, `.ogg`, `.wma`, `.aac`, `.ape`, `.alac`, `.mid`, `.ac3`, `.amr`, `.ra`, `.opus`

_(Only some formats, such as `.jpg`, `.png`, `.gif`, `.mp4`, and `.opus`, have been tested. Others are included for broader support but may not be verified.)_

## Keybindings

| Key | Action |
|-----|--------|
| `A` | Set destination folder |
| `L` | Load folder |
| `P` | Play/Pause (for audio/video files) |
| `O` | Open file in an external player |
| `← (Left Arrow)` | Go to the previous file |
| `→ (Right Arrow)` | Go to the next file |
| `Ctrl + Z` | Undo last move |
| `Right Click (in the photo)` | show where the file located |
| `Right Click (in log area)` | show where the file located/preview image |

---
