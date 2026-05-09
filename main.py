"""
Entry point — builds the tabbed application window and starts the event loop.
"""

import tkinter as tk
from tkinter import ttk

from panels.folder_scanner import ScanTab
from panels.search_panel import SearchTab


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Shitsuji")

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if screen_w > 0 and screen_h > 0:
            win_w, win_h = screen_w // 2, screen_h // 2
        else:
            screen_w, screen_h = 1280, 720
            win_w, win_h = 640, 360
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.minsize(700, 450)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True)

        notebook.add(SearchTab(notebook), text="  Search  ")
        notebook.add(ScanTab(notebook), text="  Scan  ")


if __name__ == "__main__":
    App().mainloop()
