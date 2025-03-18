# fair-sorting
A manual photo sorter using a GUI, complete with log moving and the ability to set the destination.

The app allows you to sort your photos manually by selecting folders where you want them placed. After opening the app, you can choose a folder to load, and it automatically detects all photo, video, or audio files based on the supported formats listed [here](#supported-file-formats).

<img src=screenshot.png>

## How to Use
To use the app, download the [release](https://github.com/ripaaf/fair-sorting/releases/) version and run the `.exe` file. The UI is a basic Windows program, which may appear plain or unintuitive at first, but it becomes easier to use with experience.

1. **Load a Folder**: Select a folder containing media files.
2. **Select Destination**: Choose multiple folders as destinations for your files.
3. **Sort Files**: Use the keybindings to organize files into different folders.

> [!NOTE]  
> This app is still in pre-release, so if you encounter any errors or bugs, please report them!

## Supported File Formats
The app supports the following file formats:

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
| `Right Click (in the photo)` | Show where the file is located |
| `Right Click (in log area)` | Show where the file is located/preview image |

## Logfile Explanation
The app automatically generates a logfile upon first use. As seen in the sample image, the logfile is named `move_log_` followed by random numbers and is saved in the same directory as the program.

- **Selecting a Logfile**: You can select any logfile within the folder with a `.txt` extension. All new moves will be recorded in the selected logfile.
- **Last Created Logfile**: The app will use the last created logfile by default. However, it does not automatically use the last manually selected logfile; if you want to use a specific logfile, you must reselect it.
- **Logfile Area Features**:
  - You can select a moved file from the logfile area and undo the move.
  - Right-clicking in the logfile area allows you to view where the file was moved from and preview the image (note: this feature does not work for video and audio files).


# Enjoy sorting your files with **Fair-Sorting**! 🚀

<img src=cutecom.png>
