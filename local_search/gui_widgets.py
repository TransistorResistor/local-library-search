"""Small disclosure groups shared by desktop views."""
import tkinter as tk
from tkinter import ttk

def disclosure(parent,label):
    box=ttk.Frame(parent)
    if parent.grid_slaves():box.grid(row=20,column=0,columnspan=3,sticky='ew',pady=4)
    else:box.pack(fill='x',pady=4)
    opened=tk.BooleanVar(value=False)
    body=ttk.Frame(box)
    def toggle():
        if opened.get():body.pack(fill='x',pady=4)
        else:body.pack_forget()
    ttk.Checkbutton(box,text=label,variable=opened,command=toggle).pack(anchor='w')
    return body
