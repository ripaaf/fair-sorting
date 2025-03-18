import os
import time
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from PIL import Image
from PIL import ImageTk
import shutil
import PIL
import cv2
import keyboard
import subprocess


class PhotoVideoViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("Photo and Video Viewer")
        self.root.geometry("800x600")
        root.minsize(900, 700)

        self.root.tk_setPalette(background='#DFDFDF')

        self.file_paths = []
        self.current_index = 0
        self.destination_folders = {}

        # self.root.configure(bg="gray")

        # Zoom level (100% by default)
        self.zoom_level = 100

        # Video playback variables
        self.playing_video = False
        self.video_frame_after_id = None
        self.video_capture = None

        # Load the last used folder path from a file or use a default path
        self.last_folder_path = self.load_last_folder_path()

        # Main frame
        self.main_frame = tk.Frame(root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        #all keybinding
        keyboard.on_press_key("a",self.set_destination)
        keyboard.on_press_key("l",self.load_folder)
        keyboard.on_press_key("p", self.play_pause_video)
        self.root.bind("o", lambda event: self.open_external_player())
        keyboard.on_press_key("left", self.previous_file)
        keyboard.on_press_key("right", self.next_file)
        keyboard.add_hotkey('ctrl+z', self.undo_move)

        # Left frame with buttons
        self.left_frame = tk.Frame(self.main_frame)
        self.left_frame.pack(side=tk.LEFT, padx=10)

        # Right frame for navigation buttons and "Move" button
        self.right_frame = tk.Frame(self.main_frame)
        self.right_frame.pack(side=tk.RIGHT, padx=10)

        self.open_player_button = ttk.Button(self.right_frame, text="Open External Player", command=self.open_external_player, state=tk.DISABLED)
        self.open_player_button.grid(row=1, column=5, padx=10, pady=5, sticky=tk.E)

        self.play_pause_button = ttk.Button(self.right_frame, text="Play", command=self.play_pause_video, state=tk.DISABLED)
        self.play_pause_button.grid(row=2, column=5, padx=10, pady=5, sticky=tk.E)

        self.load_button = ttk.Button(self.left_frame, text="Load Folder", command=self.load_folder)
        self.load_button.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)

        self.set_destination_button = ttk.Button(self.left_frame, text="Add Destination", command=self.set_destination)
        self.set_destination_button.grid(row=1, column=0, padx=10, pady=5, sticky=tk.W)

        # files name
        self.file_name_label = ttk.Label(self.left_frame, text="", wraplength=200, font=("Poppins", 10))
        self.file_name_label.grid(row=2, column=0, padx=10, pady=5, sticky=tk.W)

        #source path
        self.loaded_path_folder = ttk.Label(self.left_frame, text="", wraplength=200)
        self.loaded_path_folder.grid(row=3, column=0, padx=10, pady=5, sticky=tk.W)

        # moved messege to spesified path
        self.moved_messege = ttk.Label(self.left_frame, text="", wraplength=200, foreground="red",font=("Poppins", 10))
        self.moved_messege.grid(row=4, column=0, padx=10, pady=5, sticky=tk.W)

        # Undo button
        self.undo_button = ttk.Button(self.left_frame, text="Undo", command=self.undo_move, state=tk.DISABLED)
        self.undo_button.grid(row=5, column=0, padx=10, pady=5, sticky=tk.W)

        self.undo_info = None  # Store information about the last move for undo

        # Photo/video display area
        self.display_frame = tk.Frame(self.main_frame)
        self.display_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        self.photo_label = tk.Label(self.display_frame)
        self.photo_label.pack(fill=tk.BOTH, expand=True)
        self.photo_label.bind("<Configure>", self.on_label_configure)
        self.photo_label.bind("<MouseWheel>", self.zoom)

        # Right frame for navigation buttons and "Move" button
        self.right_frame = tk.Frame(self.main_frame)
        self.right_frame.pack(side=tk.RIGHT, padx=10)

        self.prev_button = ttk.Button(self.right_frame, text="Previous", command=self.previous_file)
        self.prev_button.grid(row=0, column=0, padx=10, pady=5, sticky=tk.E)

        # index label (photo)
        self.index_label = ttk.Label(self.right_frame, text="")
        self.index_label.grid(row=0, column=1, padx=10, pady=5, sticky=tk.E)

        # self.move_button = ttk.Button(self.right_frame, text="Move", command=self.move_file, state=tk.DISABLED)
        # self.move_button.grid(row=0, column=2, padx=10, pady=5, sticky=tk.E)

        self.next_button = ttk.Button(self.right_frame, text="Next", command=self.next_file)
        self.next_button.grid(row=0, column=3, padx=10, pady=5, sticky=tk.E)

        self.choose_file_messege()


    def choose_file_messege(self):
        self.photo_label.config(text="please choose folder...\nwith jpg, jpeg, png, gif, mp4, avi, mkv")
        self.index_label.config(text="0 / 0 photos")
        self.file_name_label.config(text="File Name : \n...")
        self.loaded_path_folder.config(text="loaded folder from \n ..")


    def load_folder(self,event=None):
        folder_path = filedialog.askdirectory(initialdir=self.last_folder_path)
        if folder_path:
            self.file_paths = []
            for file in os.listdir(folder_path):
                file_path = os.path.join(folder_path, file)
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', '.mkv')):
                    self.file_paths.append(file_path)

            self.current_index = 0
            self.destination_folders = {}
            self.save_last_folder_path(folder_path)
            self.loaded_path_folder.config(text="loaded folder from \n " + folder_path)

            if not self.file_paths:
                # No supported files found in the folder
                self.photo_label.config(image='')
                self.photo_label.config(text="No files with supported extensions found in the folder.")
                self.index_label.config(text="0 / 0 photos")
                self.file_name_label.config(text="File Name : \n..")
                self.play_pause_button.config(state=tk.DISABLED)
                self.stop_video()
            else:
                self.show_current_file()  # Display the first file if found
                # self.create_move_button()



    def load_last_folder_path(self):
        try:
            with open("last_folder.txt", "r") as file:
                folder_path = file.read().strip()
                print("Loaded last folder path:", folder_path)
                return folder_path
        except FileNotFoundError:
            print("last_folder.txt not found")
            return os.path.expanduser("~")  # Use the user's home directory as a default path


    def save_last_folder_path(self, folder_path):
        with open("last_folder.txt", "w") as file:
            file.write(folder_path)

    def set_destination(self,event=None):
        destination_folder = filedialog.askdirectory()
        if destination_folder:
            self.destination_folders[destination_folder] = []
            self.create_move_button(destination_folder)

    def add_destination(self):
        destination_folder = filedialog.askdirectory()
        if destination_folder:
            self.destination_folders[destination_folder] = []
            self.create_move_button(destination_folder)

    def create_move_button(self, destination_folder):
        button_frame = tk.Frame(self.right_frame)
        button_frame.grid(row=len(self.destination_folders), column=0, padx=10, pady=5, sticky=tk.E)
        
        button_index = len(self.destination_folders) + 1  # 1-based index
        
        move_button = ttk.Button(button_frame, text=f"Move to {destination_folder}", command=lambda folder=destination_folder: self.move_file(folder))
        move_button.pack(side=tk.LEFT)
        
        remove_button = ttk.Button(button_frame, text="❌", command=lambda folder=destination_folder: self.remove_destination(destination_folder))
        remove_button.pack(side=tk.LEFT)

        key_label = ttk.Label(button_frame, text="keybind : " + str(button_index))
        key_label.pack(side=tk.LEFT)
        
        # Bind keys 1, 2, 3, ... to the move buttons
        self.root.bind(str(button_index), lambda event, button=move_button: button.invoke())
        
        self.destination_folders[destination_folder] = (move_button, remove_button, key_label)

    def remove_destination(self, destination_folder):
        if destination_folder in self.destination_folders:
            move_button, remove_button, key_label = self.destination_folders[destination_folder]
            move_button.destroy()
            remove_button.destroy()
            key_label.destroy()
            del self.destination_folders[destination_folder]


    # def update_move_button_state(self):
    #     if self.destination_folders:
    #         self.move_button.config(state=tk.NORMAL)
    #     else:
    #         self.move_button.config(state=tk.DISABLED)

    def show_current_file(self):
        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            file_name = os.path.basename(file_path)  # Get the file name

            try:
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    img = Image.open(file_path)

                    # Calculate the dimensions of the label (the photo frame)
                    label_width = self.photo_label.winfo_width()
                    label_height = self.photo_label.winfo_height()

                    # Calculate the aspect ratio of the image
                    image_aspect_ratio = img.width / img.height

                    # Calculate the width and height to fit the label while preserving aspect ratio
                    if label_width / image_aspect_ratio <= label_height:
                        width_scale = label_width
                        height_scale = label_width / image_aspect_ratio
                    else:
                        width_scale = label_height * image_aspect_ratio
                        height_scale = label_height

                    # Apply the zoom level (if desired)
                    width_scale *= (self.zoom_level / 100)
                    height_scale *= (self.zoom_level / 100)

                    # Resize the image to fit within the label
                    img = img.resize((int(width_scale), int(height_scale)), Image.ANTIALIAS)

                    img = ImageTk.PhotoImage(img)
                    self.photo_label.config(image=img)
                    self.photo_label.image = img
                    self.file_name_label.config(text="File Name : \n " + file_name)
                    self.play_pause_button.config(state=tk.DISABLED)
                    self.open_player_button.config(state=tk.NORMAL)
                    self.stop_video()

                elif file_path.lower().endswith(('.mp4', '.avi', '.mkv')):
                    # self.display_video(file_path, file_name)
                    self.open_external_player()
                    self.photo_label.config(image='')
                    self.photo_label.config(text="Video opens in an external player..")
                    self.play_pause_button.config(state=tk.NORMAL)
                    self.open_player_button.config(state=tk.NORMAL)

                else:
                    self.photo_label.config(image='')
                    self.photo_label.config(text="Unsupported File Type")
                    self.file_name_label.config(text="File Name : \n" + file_name)
                    self.play_pause_button.config(state=tk.DISABLED)
                    self.open_player_button.config(state=tk.DISABLED)
                    self.stop_video()

                # Set the window title to the file name
                self.root.title(file_name)

                # Update the index label
                self.index_label.config(text=f"{self.current_index + 1} / {len(self.file_paths)} photos")

            except PIL.UnidentifiedImageError:
                self.photo_label.config(image='')
                self.photo_label.config(text="Unable to open the image file.")
                self.file_name_label.config(text="")
                self.play_pause_button.config(state=tk.DISABLED)
                self.stop_video()



    def display_video(self, video_path, file_name):
        self.stop_video()  # Stop any ongoing video playback

        try:
            # Initialize the video capture object
            self.video_capture = cv2.VideoCapture(video_path)

            if not self.video_capture.isOpened():
                self.play_pause_button.config(state=tk.DISABLED)  # Disable the Play button if no video is detected
                return

            def update_frame():
                if self.video_capture is not None:  # Check if the video capture object is still valid
                    ret, frame = self.video_capture.read()
                    if ret:
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                        # Get the dimensions of the label
                        label_width = self.photo_label.winfo_width()
                        label_height = self.photo_label.winfo_height()

                        # Calculate the aspect ratio of the video frame
                        video_aspect_ratio = frame_rgb.shape[1] / frame_rgb.shape[0]

                        # Calculate the width and height based on the label's dimensions while preserving the aspect ratio
                        if label_width / video_aspect_ratio <= label_height:
                            # If the label's width is the constraining factor
                            width_scale = label_width
                            height_scale = label_width / video_aspect_ratio
                        else:
                            # If the label's height is the constraining factor
                            width_scale = label_height * video_aspect_ratio
                            height_scale = label_height

                        # Apply the zoom level (if desired)
                        width_scale *= (self.zoom_level / 100)
                        height_scale *= (self.zoom_level / 100)

                        # Resize the frame to fit within the label
                        resized_frame = cv2.resize(frame_rgb, (int(width_scale), int(height_scale)))

                        img = Image.fromarray(resized_frame)
                        img = ImageTk.PhotoImage(img)
                        self.photo_label.config(image=img)
                        self.photo_label.image = img
                        self.file_name_label.config(text=file_name)

                        # Update the video frame
                        self.video_frame = img

                        if self.playing_video:
                            self.root.after(30, update_frame)  # Update frame every 30 milliseconds
                    else:
                        self.playing_video = False
                        self.play_pause_button.config(text="Play")
                        self.play_pause_button.config(state=tk.NORMAL)  # Enable the Play button when video playback ends
                        if self.video_capture is not None:
                            self.video_capture.release()
        
            self.playing_video = True
            self.play_pause_button.config(text="Pause")
            update_frame()
        
        except Exception as e:
            # Handle any exception that occurs during video playback
            self.photo_label.config(image='')
            print(f"Error during video playback: {str(e)}")
            self.playing_video = False
            self.play_pause_button.config(text="Play")
            self.play_pause_button.config(state=tk.NORMAL)  # Enable the Play button



    def open_external_player(self):
        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            file_name = os.path.basename(file_path)  # Get the file name
            self.file_name_label.config(text='File Name : \n' + file_name)
            if file_path.lower().endswith(('.mp4', '.avi', '.mkv','.png','.jpeg','.jpg','.gif')):
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
        self.video_capture = None  # Release the video capture object


    def play_pause_video(self, event=None):
        if self.playing_video:
            self.stop_video()
        else:
            self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))

    def move_file(self, destination_folder):
        if 0 <= self.current_index < len(self.file_paths):
            file_path = self.file_paths[self.current_index]
            destination_path = os.path.join(destination_folder, os.path.basename(file_path))

            # Store information about the move for undo
            self.undo_info = {
                "source_path": file_path,
                "destination_path": destination_path,
            }

            shutil.move(file_path, destination_path)
            self.file_paths.pop(self.current_index)
            if self.current_index >= len(self.file_paths):
                self.current_index = 0
            self.show_current_file()
            self.undo_button.config(state=tk.NORMAL)  # Enable the Undo button
            # Display a message above the filename
            message = f"{os.path.basename(file_path)} moved to {destination_folder}"
            self.moved_messege.config(text=message)
            # Clear the message after 2000 milliseconds (7 seconds)
            self.root.after(7000, self.clear_message)

    def undo_move(self):
        if self.undo_info:
            source_path = self.undo_info["source_path"]
            destination_path = self.undo_info["destination_path"]
            shutil.move(destination_path, source_path)
            self.file_paths.insert(self.current_index, source_path)
            self.current_index += 1
            self.show_current_file()
            self.undo_button.config(state=tk.DISABLED)  # Disable the Undo button
            self.moved_messege.config(text=f"Undo : {os.path.basename(destination_path)} moved back to {os.path.dirname(destination_path)}")
            self.undo_info = None  # Clear the undo information
            self.root.after(7000, self.clear_message)

    def clear_message(self):
        # Clear the message displayed above the filename
        self.moved_messege.config(text="")


    def previous_file(self, event=None):
        if not self.file_paths:
            self.photo_label.config(text="No files loaded. Please select a folder.")
            self.root.after(2000, self.choose_file_messege)
            return

        self.stop_video()  # Stop the video playback
        if self.current_index > 0:
            self.current_index -= 1
        else:
            self.current_index = len(self.file_paths) - 1  # Go to the last photo if already at the first photo
        self.show_current_file()

    def next_file(self, event=None):
        if not self.file_paths:
            self.photo_label.config(text="No files loaded. Please select a folder.")
            self.root.after(2000, self.choose_file_messege)
            return

        self.stop_video()  # Stop the video playback
        if self.current_index < len(self.file_paths) - 1:
            self.current_index += 1
        else:
            self.current_index = 0  # Go to the first photo if already at the last photo
        self.show_current_file()



    def on_label_configure(self, event):
        if self.playing_video and self.video_frame:
            self.photo_label.config(image=self.video_frame)
            self.photo_label.image = self.video_frame


    def zoom(self, event):
        # Handle zoom in (scroll up) and zoom out (scroll down)
        if event.delta > 0:
            self.zoom_level += 10
        else:
            self.zoom_level -= 10

        if self.zoom_level < 10:
            self.zoom_level = 10  # Limit minimum zoom level

        if self.playing_video:
            # If a video is currently playing, simply update the zoom level
            self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))
        elif self.file_paths and self.file_paths[self.current_index].lower().endswith(('.mp4', '.avi', '.mkv')):
            # If it's a video file, update the zoom level without restarting
            self.display_video(self.file_paths[self.current_index], os.path.basename(self.file_paths[self.current_index]))
        else:
            # If it's an image, re-display the current file with the new zoom level
            self.show_current_file()


if __name__ == "__main__":
    root = tk.Tk()
    app = PhotoVideoViewer(root)
    root.mainloop()
    