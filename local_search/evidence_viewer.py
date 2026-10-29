"""Read-only examination of the actual tool passages saved with an answer."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from .conversation_evidence import fragments
from .policy import source_ids


def show(app,source,result):
    window=tk.Toplevel(app.root);window.title('Cited evidence');window.geometry('880x650')
    ttk.Label(window,text=source['title'],font=('Segoe UI',12,'bold'),wraplength=820,padding=10).pack(anchor='w')
    previous=[entry['result'] for entry in result.get('trace',[]) if entry.get('tool')=='previous_turn']
    reused=any(source['id'] in source_ids(item) for item in previous)
    note='Reused from the previous answer. ' if reused else ''
    ttk.Label(window,text=note+'Saved passages supplied to this answer; unchanged evidence does not rule out newer contradictory reports.',wraplength=820,padding=(10,0)).pack(anchor='w')
    text=ScrolledText(window,wrap='word',font=('Segoe UI',10),padx=12,pady=10);text.pack(fill='both',expand=True,padx=10,pady=10)
    text.insert('end','\n'.join(f'{key}: {source[key]}' for key in ('locator','published_at','modified_at','added_at','page') if source.get(key))+'\n\n')
    results=[entry.get('result') for entry in result.get('trace',[]) if isinstance(entry.get('result'),dict)]
    results += [item['result'] for item in result.get('conversation_evidence',{}).get('items',[])]
    seen=set()
    for fragment in fragments(results,[source['id']]):
        blocks=fragment.get('results',[fragment])
        for block in blocks:
            body=block.get('text','')
            if body and body not in seen:
                seen.add(body);pages=sorted({loc['page'] for loc in block.get('locations',[]) if type(loc.get('page')) is int})
                text.insert('end',('PDF pages '+str(pages)+'\n' if pages else '')+body+'\n\n')
    if not seen:text.insert('end','No passage body was retained for this citation. Open the original to inspect it.')
    text.configure(state='disabled')
    ttk.Button(window,text='Open original PDF / web source',command=lambda:app.open_link(source)).pack(anchor='w',padx=10,pady=(0,10))
