"""A user-owned alias map for one text field; starts empty."""
import tkinter as tk
from tkinter import ttk,messagebox
from .metadata_aliases import compile_aliases


class AliasEditor:
    def __init__(self,parent,aliases,on_save):
        self.aliases=dict(aliases);self.on_save=on_save;self.dirty=False;self.loading=False
        self.window=tk.Toplevel(parent);self.window.title('Field alias map');self.window.geometry('760x500');self.window.minsize(600,360)
        ttk.Label(self.window,text='Map complete field values to a preferred name. Matching ignores case and repeated whitespace. Quotes stay unchanged. Only rules you add are used.',wraplength=700,padding=12).pack(anchor='w')
        self.rows=ttk.Treeview(self.window,columns=('alias','canonical'),show='headings',selectmode='browse')
        self.rows.heading('alias',text='Source spelling / alias');self.rows.heading('canonical',text='Preferred name');self.rows.pack(fill='both',expand=True,padx=12)
        self.rows.bind('<<TreeviewSelect>>',self.select)
        form=ttk.Frame(self.window,padding=12);form.pack(side='bottom',fill='x',before=self.rows);form.columnconfigure(1,weight=1)
        self.alias=tk.StringVar();self.target=tk.StringVar()
        for row,(label,var) in enumerate([('Alias',self.alias),('Preferred name',self.target)]):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=(0,8));ttk.Entry(form,textvariable=var).grid(row=row,column=1,sticky='ew',pady=4)
        bar=ttk.Frame(self.window,padding=12);bar.pack(side='bottom',fill='x',before=form)
        for label,callback in [('Add / update rule',self.add),('Remove selected',self.remove),('Use this map',self.save)]:ttk.Button(bar,text=label,command=callback).pack(side='left',padx=4)
        self.draw()
        for var in (self.alias,self.target):var.trace_add('write',self.changed)
        self.window.protocol('WM_DELETE_WINDOW',self.close)

    def changed(self,*args):
        if not self.loading:self.dirty=True

    def close(self):
        if self.dirty and not messagebox.askyesno('Unsaved aliases','Discard alias map edits that have not been applied to the profile?',parent=self.window):return False
        self.window.destroy();return True

    def draw(self):
        self.rows.delete(*self.rows.get_children());self.keys=list(self.aliases)
        for index,alias in enumerate(self.keys):self.rows.insert('','end',iid=str(index),values=(alias,self.aliases[alias]))

    def select(self,event=None):
        selected=self.rows.selection()
        if selected:
            self.loading=True;name=self.keys[int(selected[0])];self.alias.set(name);self.target.set(self.aliases[name]);self.loading=False

    def add(self):
        try:
            candidate={**self.aliases,self.alias.get().strip():self.target.get().strip()};compile_aliases(candidate)
            self.aliases=candidate;self.dirty=True;self.draw();return True
        except ValueError as error:messagebox.showerror('Alias rule',str(error),parent=self.window);return False

    def remove(self):
        selected=self.rows.selection()
        if selected:self.aliases.pop(self.keys[int(selected[0])]);self.dirty=True;self.alias.set('');self.target.set('');self.draw()

    def save(self):
        if (self.alias.get().strip() or self.target.get().strip()) and not self.add():return
        self.on_save(dict(self.aliases));self.window.destroy()
