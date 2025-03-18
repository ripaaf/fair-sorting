import datetime
import math
import os
import shutil
import subprocess
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

import cv2
import pygame
from PIL import Image, ImageTk, UnidentifiedImageError


class PhotoVideoViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("Fair Sorting")
        icon_path = os.path.join(os.path.dirname(__file__), 'cocute.ico')
        self.root.iconbitmap(icon_path)
        self.root.geometry("1000x700")
        root.minsize(1000, 700)
        self.root.tk_setPalette(background='#F0F0F0')

        self.file_paths = []
        self.current_index = 0
        self.destination_folders = {}
        self.next_previews = []
        self.prev_previews = []
        self.zoom_level = 100
        self.playing_video = False
        self.video_frame_after_id = None
        self.video_capture = None
        self.undo_info = None

        # Initialize logfile
        self.logfile = self.create_default_logfile()

        self.main_frame = tk.Frame(root, bg='#F0F0F0')
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.setup_keybindings()
        self.setup_left_frame()
        self.setup_right_frame()
        self.setup_display_frame()
        self.setup_log_area()

        self.choose_file_messege()
        self.load_previews()
        self.last_folder_path = self.load_last_folder_path()

    def create_default_logfile(self):
        default_logfile = f"move_log_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
        return default_logfile

    def setup_keybindings(self):
        self.root.bind("a", lambda event: self.set_destination())
        self.root.bind("l", lambda event: self.load_folder())
        self.root.bind("p", lambda event: self.play_pause())
        self.root.bind("o", lambda event: self.open_external_player())
        self.root.bind("<Left>", lambda event: self.previous_file())
        self.root.bind("<Right>", lambda event: self.next_file())
        self.root.bind("<Control-z>", lambda event: self.undo_move())

    def setup_left_frame(self):
        self.left_frame = tk.Frame(self.main_frame, bg='#F0F0F0')
        self.left_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.Y)

        self.load_button = ttk.Button(self.left_frame, text="Load Folder", command=self.load_folder)
        self.load_button.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)

        self.set_destination_button = ttk.Button(self.left_frame, text="Add Destination", command=self.set_destination)
        self.set_destination_button.grid(row=1, column=0, padx=10, pady=5, sticky=tk.W)

        self.file_name_label = ttk.Label(self.left_frame, text="", wraplength=200, font=("Poppins", 10), background='#F0F0F0')
        self.file_name_label.grid(row=2, column=0, padx=10, pady=5, sticky=tk.W)

        self.file_info_label = ttk.Label(self.left_frame, text="", wraplength=200, font=("Poppins", 10), background='#F0F0F0')
        self.file_info_label.grid(row=6, column=0, padx=10, pady=5, sticky=tk.W)

        self.loaded_path_folder = ttk.Label(self.left_frame, text="", wraplength=200, background='#F0F0F0')
        self.loaded_path_folder.grid(row=3, column=0, padx=10, pady=5, sticky=tk.W)

        self.moved_messege = ttk.Label(self.left_frame, text="", wraplength=200, foreground="red", font=("Poppins", 10), background='#F0F0F0')
        self.moved_messege.grid(row=4, column=0, padx=10, pady=5, sticky=tk.W)

        self.undo_button = ttk.Button(self.left_frame, text="Undo", command=self.undo_move, state=tk.DISABLED)
        self.undo_button.grid(row=5, column=0, padx=10, pady=5, sticky=tk.W)

    def setup_right_frame(self):
        self.right_frame = tk.Frame(self.main_frame, bg='#F0F0F0')
        self.right_frame.pack(side=tk.RIGHT, padx=10, pady=10, fill=tk.Y)

        self.open_player_button = ttk.Button(self.right_frame, text="Open External Player", command=self.open_external_player, state=tk.DISABLED)
        self.open_player_button.grid(row=1, column=0, padx=10, pady=5, sticky=tk.E)

        self.play_pause_button = ttk.Button(self.right_frame, text="Play", command=self.play_pause, state=tk.DISABLED)
        self.play_pause_button.grid(row=2, column=0, padx=10, pady=5, sticky=tk.E)

        self.buat_move_button = ttk.Label(self.right_frame, background='#F0F0F0')
        self.buat_move_button.grid(row=3, column=0, padx=10, pady=5, sticky=tk.E)

    def setup_display_frame(self):
        self.display_frame = tk.Frame(self.main_frame, bg='#F0F0F0')
        self.display_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Configure grid layout
        self.display_frame.grid_rowconfigure(0, weight=1)  # Photo label expands
        self.display_frame.grid_rowconfigure(1, weight=0)  # Navigation stays fixed
        self.display_frame.grid_columnconfigure(0, weight=1)

        # Photo label
        self.photo_label = tk.Label(self.display_frame, bg='#E2E2E2')
        self.photo_label.grid(row=0, column=0, sticky="nsew")
        self.photo_label.bind("<Configure>", self.on_label_configure)
        self.photo_label.bind("<MouseWheel>", self.zoom)

        # Navigation Frame (Fixed at the bottom)
        self.nav_frame = tk.Frame(self.display_frame, bg='#F0F0F0')
        self.nav_frame.grid(row=1, column=0, sticky="ew", pady=10)

        # Ensure buttons expand, but label stays centered
        self.nav_frame.grid_columnconfigure(0, weight=1)  # Previous button expands
        self.nav_frame.grid_columnconfigure(1, weight=0)  # Index label stays centered
        self.nav_frame.grid_columnconfigure(2, weight=1)  # Next button expands

        # Navigation buttons
        self.prev_button = ttk.Button(self.nav_frame, text="Previous", command=self.previous_file)
        self.prev_button.grid(row=0, column=0, padx=10, pady=5, sticky="ew")

        self.index_label = ttk.Label(self.nav_frame, text="1/10", background='#F0F0F0', anchor="center")
        self.index_label.grid(row=0, column=1, padx=10, pady=5)

        self.next_button = ttk.Button(self.nav_frame, text="Next", command=self.next_file)
        self.next_button.grid(row=0, column=2, padx=10, pady=5, sticky="ew")

    def setup_log_area(self):
        self.log_frame = tk.Frame(self.left_frame, bg='#F0F0F0')
        self.log_frame.grid(row=8, column=0, padx=10, pady=5, sticky=tk.W)

        # Create a frame to hold both buttons
        self.logfile_buttons_frame = tk.Frame(self.left_frame, bg='#F0F0F0')
        self.logfile_buttons_frame.grid(row=6, column=0, padx=10, pady=5, sticky=tk.W)

        self.select_logfile_button = ttk.Button(self.logfile_buttons_frame, text="Select Logfile", command=self.select_logfile)
        self.select_logfile_button.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        self.use_last_logfile_button = ttk.Button(self.logfile_buttons_frame, text="Last Logfile", command=self.use_last_logfile)
        self.use_last_logfile_button.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        self.logfile_name_label = ttk.Label(self.log_frame, text=f"name: {self.logfile}", background='#F0F0F0')
        self.logfile_name_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        self.log_label = ttk.Label(self.log_frame, text="Move Log:", background='#F0F0F0')
        self.log_label.grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)

        self.log_listbox = tk.Listbox(self.log_frame, height=10, width=50)
        self.log_listbox.grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.log_listbox.bind('<Button-3>', self.show_log_context_menu)

        self.log_undo_button = ttk.Button(self.log_frame, text="Undo Selected", command=self.undo_selected_move, state=tk.DISABLED)
        self.log_undo_button.grid(row=3, column=0, padx=5, pady=5, sticky=tk.W)

        self.log_listbox.bind('<<ListboxSelect>>', self.on_log_select)
        self.log_frame.config(highlightbackground="#D0D0D0", highlightcolor="#D0D0D0", highlightthickness=1)
        self.load_log()
        self.create_log_context_menu()

    def create_log_context_menu(self):
        self.log_context_menu = tk.Menu(self.root, tearoff=0)
        self.log_context_menu.add_command(label="Show Details", command=lambda: None)
        self.log_context_menu.add_command(label="Preview Image", command=self.preview_image)

    def show_log_context_menu(self, event):
        try:
            self.log_listbox.selection_clear(0, tk.END)
            self.log_listbox.selection_set(self.log_listbox.nearest(event.y))
            self.log_listbox.activate(self.log_listbox.nearest(event.y))

            selected_index = self.log_listbox.curselection()
            if selected_index:
                log_entry = self.log_listbox.get(selected_index)
                with open(self.logfile, "r") as log_file:
                    lines = log_file.readlines()
                for line in lines:
                    if os.path.basename(line.split(" -> ")[0]) == log_entry:
                        source_path = line.split(" -> ")[0].strip()
                        destination_path = line.split(" -> ")[1].strip()
                        break

                self.log_context_menu.entryconfig("Show Details", command=lambda: self.show_move_details(source_path, destination_path))
                self.log_context_menu.entryconfig("Preview Image", command=lambda: self.preview_image(destination_path))
            self.log_context_menu.post(event.x_root, event.y_root)
        finally:
            self.log_context_menu.grab_release()

    def preview_image(self, file_path):
        preview_window = tk.Toplevel(self.root)
        preview_window.title("Image Preview")
        preview_window.geometry("800x600")
        preview_window.tk_setPalette(background='#F0F0F0')

        img = Image.open(file_path)
        img.thumbnail((800, 600), Image.LANCZOS)
        img = ImageTk.PhotoImage(img)

        img_label = tk.Label(preview_window, image=img, bg='#F0F0F0')
        img_label.image = img
        img_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def resize_image(event):
            new_width = event.width - 20  # Adjust for padding
            new_height = event.height - 20  # Adjust for padding
            resized_img = Image.open(file_path)
            resized_img.thumbnail((new_width, new_height), Image.LANCZOS)
            resized_img = ImageTk.PhotoImage(resized_img)
            img_label.config(image=resized_img)
            img_label.image = resized_img

        preview_window.bind('<Configure>', resize_image)

    def show_move_details(self, source_path, destination_path):
        details_window = tk.Toplevel(self.root)
        details_window.title("Move Details")
        details_window.geometry("400x200")
        details_window.tk_setPalette(background='#F0F0F0')

        details_frame = tk.Frame(details_window, bg='#F0F0F0')
        details_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        source_label = ttk.Label(details_frame, text=f"Source: {source_path}", wraplength=380, background='#F0F0F0')
        source_label.pack(pady=5)

        destination_label = ttk.Label(details_frame, text=f"Destination: {destination_path}", wraplength=380, background='#F0F0F0')
        destination_label.pack(pady=5)

    def choose_file_messege(self):
        self.photo_label.config(text="please choose folder...\n with photo, video, and music")
        self.index_label.config(text="0 / 0 photos")
        self.file_name_label.config(text="File Name : \n...")
        self.loaded_path_folder.config(text="loaded folder : \n ..")

    def load_folder(self, event=None):
        folder_path = filedialog.askdirectory(initialdir=self.last_folder_path)
        if folder_path:
            self.file_paths = [os.path.join(folder_path, file) for file in os.listdir(folder_path) if file.lower().endswith(
                ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.3gp', '.wmv', '.rmvb', '.m4v',
                 '.mpeg', '.divx', '.ogv', '.ts', '.m2ts', '.vob', '.mts', '.flac', '.mkv', '.mpg', '.asf', '.rm', '.ogm', '.bmp',
                 '.tiff', '.psd', '.svg', '.raw', '.heic', '.cr2', '.nef', '.orf', '.arw', '.rw2', '.dng', '.xcf', '.pcx', '.mp3',
                 '.wav', '.m4a', '.ogg', '.wma', '.aac', '.ape', '.alac', '.mid', '.ac3', '.amr', '.ra', '.ogg', '.opus'))]

            self.current_index = 0
            self.destination_folders = {}
            self.update_layout()
            self.save_last_folder_path(folder_path)
            self.loaded_path_folder.config(text="loaded folder : \n " + folder_path)

            if not self.file_paths:
                self.photo_label.config(image='')
                self.photo_label.config(text="No files with supported extensions found in the folder.")
                self.index_label.config(text="0 / 0 photos")
                self.file_name_label.config(text="File Name : \n..")
                self.play_pause_button.config(state=tk.DISABLED)
                self.stop_video()
            else:
                self.show_current_file()

    def load_previews(self):
        for i in range(1, 11):
            next_index = self.current_index + i
            if next_index < len(self.file_paths):
                self.next_previews.append(self.load_image_preview(self.file_paths[next_index]))

            prev_index = self.current_index - i
            if prev_index >= 0:
                self.prev_previews.append(self.load_image_preview(self.file_paths[prev_index]))

    def load_last_folder_path(self):
        try:
            with open("last_folder.txt", "r") as file:
                folder_path = file.read().strip()
                print(f"Attempting to load folder: {folder_path}")
                self.load_folder_from_path(folder_path)
                return folder_path
        except FileNotFoundError:
            return os.path.expanduser("~")

    def load_folder_from_path(self, folder_path):
        if folder_path:
            self.file_paths = [os.path.join(folder_path, file) for file in os.listdir(folder_path) if file.lower().endswith(
                ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.3gp', '.wmv', '.rmvb', '.m4v',
                 '.mpeg', '.divx', '.ogv', '.ts', '.m2ts', '.vob', '.mts', '.flac', '.mkv', '.mpg', '.asf', '.rm', '.ogm', '.bmp',
                 '.tiff', '.psd', '.svg', '.raw', '.heic', '.cr2', '.nef', '.orf', '.arw', '.rw2', '.dng', '.xcf', '.pcx', '.mp3',
                 '.wav', '.m4a', '.ogg', '.wma', '.aac', '.ape', '.alac', '.mid', '.ac3', '.amr', '.ra', '.ogg', '.opus'))]

            self.current_index = 0
            self.destination_folders = {}
            self.loaded_path_folder.config(text="loaded folder : \n " + folder_path)

            if not self.file_paths:
                self.photo_label.config(image='')
                self.photo_label.config(text="No files with supported extensions found in the folder.")
                self.index_label.config(text="0 / 0 photos")
                self.file_name_label.config(text="File Name : \n..")
                self.play_pause_button.config(state=tk.DISABLED)
                self.stop_video()
            else:
                self.show_current_file()

    def save_last_folder_path(self, folder_path):
        with open("last_folder.txt", "w") as file:
            file.write(folder_path)

    def set_destination(self, event=None):
        destination_folder = filedialog.askdirectory()
        if destination_folder:
            self.destination_folders[destination_folder] = []
            self.create_move_button(destination_folder, self.buat_move_button)

    def add_destination(self):
        destination_folder = filedialog.askdirectory()
        if destination_folder:
            self.destination_folders[destination_folder] = []
            self.create_move_button(destination_folder, self.buat_move_button)

    def create_move_button(self, destination_folder, parent_frame):
        button_frame = tk.Frame(parent_frame)
        button_frame.grid(row=len(self.destination_folders), column=0, padx=0, pady=0, sticky=tk.E)
        button_index = len(self.destination_folders) + 1
        folder_name = os.path.basename(destination_folder)

        move_button = ttk.Button(button_frame, text=f"Move to {folder_name}", command=lambda folder=destination_folder: self.move_file(folder))
        move_button.grid(row=0, column=2, padx=0, pady=10)

        remove_button = ttk.Button(button_frame, text="X", width=2, command=lambda folder=destination_folder: self.remove_destination(folder))
        remove_button.grid(row=0, column=1, padx=2, pady=0)

        key_label = ttk.Label(button_frame, text="key : " + str(button_index))
        key_label.grid(row=0, column=0, padx=5)

        self.root.bind(str(button_index), lambda event, button=move_button: button.invoke())

        self.destination_folders[destination_folder] = (move_button, remove_button, key_label)

    def remove_destination(self, destination_folder):
        if destination_folder in self.destination_folders:
            move_button, remove_button, key_label = self.destination_folders[destination_folder]
            move_button.destroy()
            remove_button.destroy()
            key_label.destroy()
            del self.destination_folders[destination_folder]
            self.update_layout()

    def update_layout(self):
        for i, folder in enumerate(self.destination_folders.keys()):
            move_button, remove_button, key_label = self.destination_folders[folder]
            move_button.grid(row=i, column=2, padx=0, pady=10, sticky=tk.E)
            remove_button.grid(row=i, column=1, padx=2, pady=0, sticky=tk.E)
            key_label.grid(row=i, column=0, padx=0, pady=5, sticky=tk.E)

    @staticmethod
    def convert_bytes(size_bytes):
        if size_bytes == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    @staticmethod
    def get_modify_date(path):
        ts = os.path.getmtime(path)
        return "Null" if ts < 0 else datetime.fromtimestamp(ts).strftime('%d.%m.%Y')

    def showImageInExplorer(self):
        if self.image_path != "":
            subprocess.run([os.path.join(os.getenv('WINDIR'), 'explorer.exe'), '/select,', os.path.normpath(self.image_path)])

    def on_label_right_click(self, event):
        self.context_menu.post(event.x_root, event.y_root)

    def create_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Show in Explorer", command=self.showImageInExplorer)

    def show_current_file(self):
        if not self.file_paths:
            self.photo_label.config(image='')
            self.photo_label.config(text="No image")
            self.index_label.config(text="0 / 0 photos")
            self.file_name_label.config(text="File Name : \n..")
            self.play_pause_button.config(state=tk.DISABLED)
            self.open_player_button.config(state=tk.DISABLED)
            self.stop_video()
            return

        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            file_name = os.path.basename(file_path)
            self.image_path = file_path
            # print(f"Attempting to load file: {file_path}")
            
            try:
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.psd', '.svg', '.raw', '.heic', '.cr2', '.nef', '.orf', '.arw', '.rw2', '.dng', '.xcf', '.pcx')):
                    self.display_image(file_path, file_name)
                elif file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.3gp', '.wmv', '.rmvb', '.m4v', '.mpeg', '.divx', '.ogv', '.ts', '.m2ts', '.vob', '.mts')):
                    self.open_external_player()
                    self.photo_label.config(image='')
                    self.photo_label.config(text=f"Video opens in an external player..\n\nFile Name: {os.path.basename(file_path)}")
                    
                    self.play_pause_button.config(state=tk.NORMAL)
                    self.open_player_button.config(state=tk.NORMAL)
                    self.create_context_menu()
                elif file_path.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.wma', '.aac', '.ape', '.alac', '.mid', '.ac3', '.amr', '.ra', '.ogg', '.opus')):
                    self.play_audio(file_path)
                else:
                    self.photo_label.config(image='')
                    self.photo_label.config(text="Unsupported File Type")
                    self.play_pause_button.config(state=tk.DISABLED)
                    self.open_player_button.config(state=tk.DISABLED)
                    self.stop_video()
                    self.create_context_menu()

                self.root.title(file_name)
                self.index_label.config(text=f"{self.current_index + 1} / {len(self.file_paths)} photos")

            except UnidentifiedImageError:
                self.photo_label.config(image='')
                self.photo_label.config(text="Unable to open the image file.")
                self.file_name_label.config(text="File Name : \n" + file_name)
                self.play_pause_button.config(state=tk.DISABLED)
                self.open_player_button.config(state=tk.DISABLED)
                self.stop_video()
                self.create_context_menu()

    def display_image(self, file_path, file_name):
        img = Image.open(file_path)
        resolution = f"Resolution: {img.width} x {img.height}"
        size = f"Size: {self.convert_bytes(os.path.getsize(file_path))}"
        date = f"Date: {self.get_modify_date(file_path)}"
        info_text = f"{resolution}\n{size}\n{date}"

        label_width = self.photo_label.winfo_width()
        label_height = self.photo_label.winfo_height()
        image_aspect_ratio = img.width / img.height

        if label_width / image_aspect_ratio <= label_height:
            width_scale = label_width
            height_scale = label_width / image_aspect_ratio
        else:
            width_scale = label_height * image_aspect_ratio
            height_scale = label_height

        width_scale = max(24, width_scale * (self.zoom_level / 100))
        height_scale = max(24, height_scale * (self.zoom_level / 100))

        img = img.resize((int(width_scale), int(height_scale)), Image.LANCZOS)
        img = ImageTk.PhotoImage(img)

        self.photo_label.config(image=img)
        self.photo_label.image = img
        self.file_name_label.config(text=f"File Name:\n{file_name}\n\n{info_text}")
        self.photo_label.bind("<Button-3>", self.on_label_right_click)
        self.play_pause_button.config(state=tk.DISABLED)
        self.open_player_button.config(state=tk.NORMAL)
        self.stop_video()
        self.create_context_menu()

    def play_audio(self, file_path):
        pygame.mixer.init()
        pygame.mixer.music.load(file_path)
        self.file_name_label.config(text=f"File Name : \n {os.path.basename(file_path)}")
        pygame.mixer.music.play()
        self.photo_label.config(image='')
        self.photo_label.config(text=f"Audio file is playing..\n\nFile Name: {os.path.basename(file_path)}")
        self.play_pause_button.config(state=tk.NORMAL)
        self.open_player_button.config(state=tk.NORMAL)
        self.create_context_menu()

    def display_video(self, video_path, file_name):
        self.stop_video()
        try:
            self.video_capture = cv2.VideoCapture(video_path)
            if not self.video_capture.isOpened():
                self.play_pause_button.config(state=tk.DISABLED)
                return

            def update_frame():
                if self.video_capture is not None:
                    ret, frame = self.video_capture.read()
                    if ret:
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                        label_width = self.photo_label.winfo_width()
                        label_height = self.photo_label.winfo_height()
                        video_aspect_ratio = frame_rgb.shape[1] / frame_rgb.shape[0]

                        if label_width / video_aspect_ratio <= label_height:
                            width_scale = label_width
                            height_scale = label_width / video_aspect_ratio
                        else:
                            width_scale = label_height * video_aspect_ratio
                            height_scale = label_height

                        width_scale *= (self.zoom_level / 100)
                        height_scale *= (self.zoom_level / 100)

                        resized_frame = cv2.resize(frame_rgb, (int(width_scale), int(height_scale)))

                        img = Image.fromarray(resized_frame)
                        img = ImageTk.PhotoImage(img)
                        self.photo_label.config(image=img)
                        self.photo_label.image = img
                        self.file_name_label.config(text=file_name)

                        if self.playing_video:
                            self.root.after(30, update_frame)
                    else:
                        self.playing_video = False
                        self.play_pause_button.config(text="Play")
                        self.play_pause_button.config(state=tk.NORMAL)
                        if self.video_capture is not None:
                            self.video_capture.release()

            self.playing_video = True
            self.play_pause_button.config(text="Pause")
            update_frame()
        except Exception as e:
            self.photo_label.config(image='')
            print(f"Error during video playback: {str(e)}")
            self.playing_video = False
            self.play_pause_button.config(text="Play")
            self.play_pause_button.config(state=tk.NORMAL)

    def open_external_player(self):
        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            file_name = os.path.basename(file_path)
            self.file_name_label.config(text='File Name : \n' + file_name)
            if file_path.lower().endswith(
                    ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', '.mkv', '.webm', '.mov', '.flv', '.3gp', '.wmv', '.rmvb', '.m4v',
                     '.mpeg', '.divx', '.ogv', '.ts', '.m2ts', '.vob', '.mts', '.flac', '.mpg', '.asf', '.rm', '.ogm', '.bmp',
                     '.tiff', '.psd', '.svg', '.raw', '.heic', '.cr2', '.nef', '.orf', '.arw', '.rw2', '.dng', '.xcf', '.pcx',
                     '.mp3', '.wav', '.m4a', '.ogg', '.wma', '.aac', '.ape', '.alac', '.mid', '.ac3', '.amr', '.ra', '.opus')):
                if os.name == 'nt':  # Windows
                    os.startfile(file_path)
                elif os.name == 'posix':  # Linux or macOS
                    subprocess.Popen(['xdg-open', file_path])
                else:
                    print("Unsupported operating system.")

    def stop_video(self):
        if self.video_capture is not None:
            self.video_capture.release()
        self.playing_video = False
        self.play_pause_button.config(text="Play")
        self.video_capture = None

    def play_pause(self, event=None):
        if self.file_paths[self.current_index].lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.wma', '.aac', '.ape', '.alac', '.mid', '.ac3', '.amr', '.ra', '.ogg', '.opus')):
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.pause()
                self.photo_label.config(text=f"Audio file paused..\n\nFile Name: {os.path.basename(self.file_paths[self.current_index])}")
            else:
                pygame.mixer.music.unpause()
                self.photo_label.config(text=f"Audio file is playing..\n\nFile Name: {os.path.basename(self.file_paths[self.current_index])}")
                self.check_audio_end()
        else:
            if self.playing_video:
                self.stop_video()
            else:
                self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))

    def check_audio_end(self):
        if not pygame.mixer.music.get_busy():
            pygame.mixer.music.play()
        else:
            self.root.after(1000, self.check_audio_end)

    def move_file(self, destination_folder):
        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            destination_path = os.path.join(destination_folder, os.path.basename(file_path))

            self.undo_info = {
                "source_path": file_path,
                "destination_path": destination_path,
            }

            shutil.move(file_path, destination_path)
            self.file_paths.pop(self.current_index)
            if self.current_index >= len(self.file_paths):
                self.current_index = 0
            self.show_current_file()

            # Log the move to a file
            with open(self.logfile, "a") as log_file:
                log_file.write(f"{file_path} -> {destination_path}\n")

            self.undo_button.config(state=tk.NORMAL)
            message = f"{os.path.basename(file_path)} moved to {destination_folder}"
            self.moved_messege.config(text=message)
            self.log_listbox.insert(tk.END, os.path.basename(file_path))
            self.root.after(7000, self.clear_message)

    def undo_move(self):
        if self.undo_info:
            source_path = self.undo_info["source_path"]
            destination_path = self.undo_info["destination_path"]
            shutil.move(destination_path, source_path)
            self.file_paths.insert(self.current_index, source_path)
            self.current_index += 1
            self.show_current_file()
            self.undo_button.config(state=tk.DISABLED)
            self.moved_messege.config(text=f"Undo: {os.path.basename(destination_path)} moved back to {os.path.dirname(destination_path)}")
            self.undo_info = None
            self.update_log_file(os.path.basename(destination_path))
            self.remove_log_entry(os.path.basename(destination_path))
            self.root.after(7000, self.clear_message)

    def undo_selected_move(self):
        selected_index = self.log_listbox.curselection()
        if selected_index:
            log_entry = self.log_listbox.get(selected_index)
            with open(self.logfile, "r") as log_file:
                lines = log_file.readlines()

            for line in lines:
                if os.path.basename(line.split(" -> ")[0]) == log_entry:
                    source_path = line.split(" -> ")[0].strip()
                    destination_path = line.split(" -> ")[1].strip()
                    break

            if os.path.exists(destination_path):
                shutil.move(destination_path, source_path)
                self.file_paths.insert(self.current_index, source_path)
                self.current_index += 1
                self.show_current_file()
                self.undo_button.config(state=tk.DISABLED)
                self.moved_messege.config(text=f"Undo: {os.path.basename(destination_path)} moved back to {os.path.dirname(destination_path)}")
                self.log_listbox.delete(selected_index)
                self.update_log_file(log_entry)
                self.undo_info = None
                self.root.after(7000, self.clear_message)

    def update_log_file(self, log_entry):
        with open(self.logfile, "r") as log_file:
            lines = log_file.readlines()

        with open(self.logfile, "w") as log_file:
            for line in lines:
                if os.path.basename(line.split(" -> ")[0]) != log_entry:
                    log_file.write(line)

    def remove_log_entry(self, log_entry):
        for i in range(self.log_listbox.size()):
            if self.log_listbox.get(i) == log_entry:
                self.log_listbox.delete(i)
                break

    def clear_log_area(self):
        self.log_listbox.delete(0, tk.END)

    def load_log(self):
        if os.path.exists(self.logfile):
            with open(self.logfile, "r") as log_file:
                for line in log_file:
                    self.log_listbox.insert(tk.END, os.path.basename(line.split(" -> ")[0]))

    def clear_message(self):
        self.moved_messege.config(text="")

    def on_log_select(self, event):
        if self.log_listbox.curselection():
            self.log_undo_button.config(state=tk.NORMAL)
        else:
            self.log_undo_button.config(state=tk.DISABLED)

    def previous_file(self, event=None):
        if not self.file_paths:
            self.photo_label.config(text="No files loaded. Please select a folder.")
            self.root.after(2000, self.choose_file_messege)
            return

        self.stop_video()
        if self.current_index > 0:
            self.current_index -= 1
        else:
            self.current_index = len(self.file_paths) - 1
        self.show_current_file()

    def next_file(self, event=None):
        if not self.file_paths:
            self.photo_label.config(text="No files loaded. Please select a folder.")
            self.root.after(2000, self.choose_file_messege)
            return

        self.stop_video()
        if self.current_index < len(self.file_paths) - 1:
            self.current_index += 1
        else:
            self.current_index = 0
        self.show_current_file()

    def on_label_configure(self, event):
        if self.playing_video and self.video_frame:
            self.photo_label.config(image=self.video_frame)
            self.photo_label.image = self.video_frame

    def zoom(self, event):
        if event.delta > 0:
            self.zoom_level += 10
        else:
            self.zoom_level -= 10

        if self.zoom_level < 10:
            self.zoom_level = 10

        if self.playing_video:
            self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))
        elif self.file_paths and self.file_paths[self.current_index].lower().endswith(('.mp4', '.avi', '.mkv')):
            self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))
        else:
            self.show_current_file()

    def select_logfile(self):
        logfile_path = filedialog.askopenfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Select Logfile"
        )
        if logfile_path:
            self.logfile = logfile_path
            self.logfile_name_label.config(text=f"name: {os.path.basename(self.logfile)}")
            self.clear_log_area()
            self.load_log()
            self.use_last_logfile_button.config(state=tk.NORMAL)

    def use_last_logfile(self):
        log_files = [f for f in os.listdir() if f.startswith("move_log_") and f.endswith(".txt")]
        if log_files:
            latest_log_file = max(log_files, key=os.path.getctime)
            self.logfile = latest_log_file
            self.logfile_name_label.config(text=f"name: {os.path.basename(self.logfile)}")
            self.clear_log_area()
            self.load_log()
            self.use_last_logfile_button.config(state=tk.DISABLED)


if __name__ == "__main__":

    root = tk.Tk()
    app = PhotoVideoViewer(root)
    root.mainloop()