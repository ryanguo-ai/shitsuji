"""
Search tab — placeholder for future search functionality.
"""

import tkinter as tk


class SearchTab(tk.Frame):

    def __init__(self, master):
        super().__init__(master, bg="#f5f5f5")
        tk.Label(
            self, text="🔍  Search",
            font=("Segoe UI", 14, "bold"),
            fg="#2c3e50", bg="#f5f5f5",
        ).pack(expand=True)
