import os
import shutil
import tkinter as tk
from tkinter import filedialog

# Global variable to track if the process should stop
stop_process = False

def move_files(source_folder, destination_folder, log_text):
    global stop_process
    log_text.insert(tk.END, f"Moving files from {source_folder} to {destination_folder}\n")
    for root, _, files in os.walk(source_folder):
        for file in files:
            if stop_process:  # Check if the stop button has been clicked
                log_text.insert(tk.END, "File moving process stopped by user.\n")
                log_text.see(tk.END)
                return

            file_path = os.path.join(root, file)
            base, extension = os.path.splitext(file)
            destination_path = os.path.join(destination_folder, file)
            
            # If the file already exists in the destination folder, append a number to the filename
            count = 1
            while os.path.exists(destination_path):
                base, extension = os.path.splitext(file)
                new_base = f"{base}_{count}"
                file = f"{new_base}{extension}"
                destination_path = os.path.join(destination_folder, file)
                count += 1

            log_text.insert(tk.END, f"Moving {file_path} to {destination_path}\n")
            log_text.update()  # Update the text widget to show the log in real-time
            log_text.see(tk.END)
            shutil.move(file_path, destination_path)
            log_text.update()  # Update the text widget to show the log in real-time
            log_text.see(tk.END)

def select_source_folder():
    folder_path = filedialog.askdirectory()
    source_folder_var.set(folder_path)

def select_destination_folder():
    folder_path = filedialog.askdirectory()
    destination_folder_var.set(folder_path)

def stop_move():
    global stop_process
    stop_process = True

def start_move():
    global stop_process
    stop_process = False
    source_folder = source_folder_var.get()
    destination_folder = destination_folder_var.get()
    if not source_folder or not destination_folder:
        log_text.insert(tk.END, "Please select source and destination folders.\n")
        return
    move_files(source_folder, destination_folder, log_text)  # Pass log_text as an argument

# GUI setup
root = tk.Tk()
root.title("File Mover")

# Initialize StringVars after root window creation
source_folder_var = tk.StringVar()
destination_folder_var = tk.StringVar()

# Source Folder Frame
source_frame = tk.Frame(root)
source_frame.pack(pady=10)

source_folder_label = tk.Label(source_frame, text="Source Folder:")
source_folder_label.grid(row=0, column=0, padx=5, pady=5)

source_folder_entry = tk.Entry(source_frame, textvariable=source_folder_var, width=50)
source_folder_entry.grid(row=0, column=1, padx=5, pady=5)

source_browse_button = tk.Button(source_frame, text="Browse", command=select_source_folder)
source_browse_button.grid(row=0, column=2, padx=5, pady=5)

# Destination Folder Frame
destination_frame = tk.Frame(root)
destination_frame.pack(pady=10)

destination_folder_label = tk.Label(destination_frame, text="Destination Folder:")
destination_folder_label.grid(row=0, column=0, padx=5, pady=5)

destination_folder_entry = tk.Entry(destination_frame, textvariable=destination_folder_var, width=50)
destination_folder_entry.grid(row=0, column=1, padx=5, pady=5)

destination_browse_button = tk.Button(destination_frame, text="Browse", command=select_destination_folder)
destination_browse_button.grid(row=0, column=2, padx=5, pady=5)

# Start and Stop Buttons
start_button = tk.Button(root, text="Start Moving", command=start_move)
start_button.pack(pady=10)

stop_button = tk.Button(root, text="Stop Moving", command=stop_move)
stop_button.pack(pady=5)

# Log Frame
log_frame = tk.Frame(root)
log_frame.pack()

log_label = tk.Label(log_frame, text="Log:")
log_label.pack()

log_text = tk.Text(log_frame, height=30, width=100)
log_text.pack()

root.mainloop()
