import os
import tkinter as tk
from tkinter import filedialog
import subprocess

class VideoPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Player")

        self.video_path = ""

        self.frame = tk.Frame(root)
        self.frame.pack(padx=10, pady=10)

        self.browse_button = tk.Button(self.frame, text="Browse", command=self.browse_video)
        self.browse_button.pack()

    def browse_video(self):
        self.video_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.avi *.mkv")])
        if self.video_path:
            self.play_video()

    def play_video(self):
        if self.video_path:
            if os.name == 'nt':  # Windows
                os.startfile(self.video_path)
            elif os.name == 'posix':  # Linux or macOS
                subprocess.Popen(['xdg-open', self.video_path])
            else:
                print("Unsupported operating system.")

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoPlayer(root)
    root.mainloop()
