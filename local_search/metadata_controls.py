"""Small sidecar control panel over the existing, separately owned metadata CLI."""
import copy
import json
import queue
import re
import subprocess
import sys
import time
import uuid
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter.scrolledtext import ScrolledText
from .metadata_profile import load_profile,validate_profile,TYPES


def command(config,operation,profile=None,n=20,seed=1,output=None,collection=''):
    if operation not in ('inspect','migrate','migrate_apply','preview','queue','sample','work'):raise ValueError('Unknown workflow operation')
    if not 1<=int(n)<=10000:raise ValueError('Choose 1–10,000 items')
    interpreter=Path(sys.executable)
    if interpreter.name.lower()=='pythonw.exe' and interpreter.with_name('python.exe').is_file():interpreter=interpreter.with_name('python.exe')
    args=[str(interpreter),'-m','local_search.metadata','--config',str(Path(config).resolve())]
    if operation=='inspect':return args+['inspect']
    if operation.startswith('migrate'):return args+['migrate']+(['--apply'] if operation=='migrate_apply' else [])
    if operation=='work':return args+['work','--limit',str(int(n)),'--output-dir',str(output)]+(['--collection',collection] if collection else [])
    args+=['run' if operation=='sample' else 'queue','--profile',str(Path(profile).resolve()),'--random-items',str(int(n)),'--seed',str(int(seed))]
    if collection:args+=['--collection',collection]
    if operation=='sample':args+=['--dry-run','--output-dir',str(output)]
    if operation=='queue':args+=['--apply']
    return args


class WorkflowPanel:
    def __init__(self,parent,config_path):
        self.window=tk.Toplevel(parent);self.window.title('Metadata extraction controls');self.window.geometry('860x680')
        self.config=Path(config_path).resolve();self.process=None;self.events=queue.Queue();self.completed=0;self.retries=0;self.accepted=0;self.last_report=None;self.counts={};self.operation=None;self.retry_failed=tk.BooleanVar(value=False);self.editors=[]
        self.profile=tk.StringVar(value=str(self.config.parent/'metadata-profile.json'));self.n=tk.StringVar(value='20');self.seed=tk.StringVar(value='1');self.collection=tk.StringVar();self.output=tk.StringVar(value=str(self.config.parent/'metadata-reports'));self.status=tk.StringVar(value='Choose a profile, run a random trial, review quality, then queue a bounded batch.')
        frame=ttk.Frame(self.window,padding=12);frame.pack(fill='x');frame.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate([('Field profile',self.profile),('Random items / batch limit',self.n),('Sample seed',self.seed),('Collection (blank = all)',self.collection),('Report folder',self.output)]):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=4);ttk.Entry(frame,textvariable=var).grid(row=row,column=1,sticky='ew',padx=8)
        ttk.Button(frame,text='Browse',command=self.choose_profile).grid(row=0,column=2)
        ttk.Button(frame,text='Edit fields / prompts…',command=self.edit_profile).grid(row=1,column=2)
        bar=ttk.Frame(self.window,padding=12);bar.pack(fill='x')
        for label,op in [('Status','inspect'),('Preview migration','migrate'),('Apply migration','migrate_apply'),('Random dry run','sample')]:ttk.Button(bar,text=label,command=lambda op=op:self.start(op)).pack(side='left',padx=3)
        bar=ttk.Frame(self.window,padding=(12,0));bar.pack(fill='x')
        for label,op in [('Preview queue','preview'),('Queue sample','queue'),('Run queued batch','work')]:ttk.Button(bar,text=label,command=lambda op=op:self.start(op)).pack(side='left',padx=3)
        ttk.Button(bar,text='Stop process',command=self.stop).pack(side='left',padx=3)
        ttk.Button(bar,text='Open report',command=self.open_report).pack(side='right')
        ttk.Checkbutton(self.window,text='Include failed jobs when running a queued batch',variable=self.retry_failed).pack(anchor='w',padx=12,pady=4)
        ttk.Label(self.window,text='Dry runs do not write metadata. Queue/migration/batch actions are explicit. Samples use the selected profile and seed; batches use each queued job’s saved profile and the collection filter. Close the main app before a managed-library write batch; only one local model owner runs at a time.',wraplength=810,padding=12).pack(anchor='w')
        self.log=ScrolledText(self.window,wrap='word',height=16,state='disabled');self.log.pack(fill='both',expand=True,padx=12)
        ttk.Label(self.window,textvariable=self.status,wraplength=810,padding=12).pack(fill='x')
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.poll_id=self.window.after(100,self.poll)

    def choose_profile(self):
        path=filedialog.askopenfilename(parent=self.window,filetypes=[('Metadata profile','*.json')])
        if path:self.profile.set(path)

    def edit_profile(self):
        try:self.editors.append(ProfileEditor(self.window,self.profile.get(),lambda path:self.profile.set(path)))
        except (OSError,ValueError) as error:messagebox.showerror('Field profile',str(error),parent=self.window)

    def start(self,operation):
        if self.operation is not None:self.status.set('Finish or stop the current process first.');return
        try:
            if operation in ('sample','preview','queue'):load_profile(self.profile.get())
            n=int(self.n.get());seed=int(self.seed.get());folder=Path(self.output.get()).expanduser().resolve()/('run-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
            args=command(self.config,operation,self.profile.get(),n,seed,folder,self.collection.get().strip())
            if operation=='work' and self.retry_failed.get():args+=['--retry-failed']
            if operation=='migrate_apply' and not messagebox.askyesno('Apply metadata migration','Apply the metadata migration with its validated backup? Close other library windows first. Preview migration shows the planned changes.',parent=self.window):return
            if operation=='queue' and not messagebox.askyesno('Queue metadata extraction',f'Queue up to {n} randomly selected items using seed {seed}? No model runs until you start a batch. Preview queue lists the candidates.',parent=self.window):return
            if operation=='work' and not messagebox.askyesno('Run metadata batch',f'Run and save findings for up to {n} queued items? Stop process leaves interrupted jobs resumable. Close the main app first if it owns this managed library.',parent=self.window):return
            self.current_output=[];self.target_count=n;self.operation=operation;self.started=time.monotonic();self.completed=0;self.retries=0;self.accepted=0;self.counts={};self.last_report=folder/'report.html' if operation in ('sample','work') else None
            self.process=subprocess.Popen(args,cwd=str(Path(__file__).resolve().parents[1]),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.status.set('Running '+operation+'…')
            import threading
            process=self.process
            def read():
                for line in process.stdout:self.events.put(('line',line))
                process.stdout.close();self.events.put(('exit',process.wait()))
            threading.Thread(target=read,daemon=True).start()
        except (OSError,ValueError) as error:
            self.operation=None;messagebox.showerror('Metadata workflow',str(error),parent=self.window)

    def poll(self):
        try:
            for _ in range(100):
                kind,value=self.events.get_nowait()
                if kind=='line':
                    if self.operation=='inspect':self.current_output.append(value)
                    self.log.configure(state='normal');self.log.insert('end',value)
                    if len(self.log.get('1.0','end-1c'))>80000:self.log.delete('1.0','end-80000c')
                    self.log.see('end');self.log.configure(state='disabled')
                    match=re.search(r'Source \d+: (\w+) \((\d+) attempts\)',value)
                    if match:
                        self.completed+=1;self.counts[match[1]]=self.counts.get(match[1],0)+1;self.retries+=int(match[2])>1;self.accepted+=match[1]=='extracted'
                else:
                    self.status.set(('Complete' if value==0 else 'Stopped / failed (exit '+str(value)+')')+' · '+str(self.completed)+' completed · '+str(self.retries)+' retried. See output for details.')
                    if value==0 and self.operation=='inspect':
                        try:
                            data=json.loads(''.join(self.current_output));self.status.set(str(data['eligible_sources'])+' eligible sources · '+('Metadata migration required' if data['metadata_schema'] is None else ' · '.join(str(item['count'])+' '+item['status'] for item in data.get('jobs',[])) or 'Queue empty'))
                        except (ValueError,KeyError):pass
                    self.operation=None
        except queue.Empty:pass
        if self.operation and self.process and self.process.poll() is None:
            elapsed=time.monotonic()-self.started;rate=self.completed/max(elapsed,.01)*60
            remaining=max(0,self.target_count-self.completed)/rate if rate else None
            self.status.set(f'{self.completed} completed · {rate:.2f} items/min · {self.retries} retried · {self.accepted} auto-extracted'+(f' · up to {remaining:.1f} min remaining' if remaining is not None else ' · estimating after first item'))
        self.poll_id=self.window.after(100,self.poll)

    def stop(self):
        if self.process and self.process.poll() is None:self.process.terminate();self.status.set('Stopping process; completed findings remain saved, interrupted jobs resume later.')

    def open_report(self):
        if self.last_report and self.last_report.is_file():
            import webbrowser
            webbrowser.open(self.last_report.as_uri())

    def close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno('Stop extraction','Stop the active process and close? Completed items remain saved; interrupted jobs can resume.',parent=self.window):return False
            self.stop()
        for editor in self.editors:
            if editor.window.winfo_exists() and not editor.close():return False
        self.window.after_cancel(self.poll_id)
        self.window.destroy();return True


class ProfileEditor:
    def __init__(self,parent,path,on_save):
        self.path=Path(path);self.profile=copy.deepcopy(load_profile(self.path if self.path.is_file() else None));self.on_save=on_save;self.dirty=False;self.loading=False
        self.window=tk.Toplevel(parent);self.window.title('Metadata fields and prompts');self.window.geometry('820x740')
        self.name=tk.StringVar(value=self.profile['name']);ttk.Label(self.window,text='Profile name').pack(anchor='w',padx=12);ttk.Entry(self.window,textvariable=self.name).pack(fill='x',padx=12)
        ttk.Label(self.window,text='Instructions for all fields').pack(anchor='w',padx=12,pady=(8,0));self.instructions=ScrolledText(self.window,height=4,wrap='word');self.instructions.pack(fill='x',padx=12);self.instructions.insert('end',self.profile['instructions'])
        self.fields=tk.Listbox(self.window,height=6);self.fields.pack(fill='x',padx=12,pady=8);self.fields.bind('<<ListboxSelect>>',self.select)
        self.field_name=tk.StringVar();self.kind=tk.StringVar(value='string');self.enum=tk.StringVar();self.maximum=tk.StringVar(value='12');self.evidence=tk.BooleanVar(value=True);self.generated=tk.BooleanVar();self.accept=tk.BooleanVar();self.scoped=tk.BooleanVar()
        frame=ttk.Frame(self.window,padding=12);frame.pack(fill='x');frame.columnconfigure(1,weight=1)
        for row,(label,var) in enumerate([('Field name (lowercase_with_underscores)',self.field_name),('Type',self.kind),('Allowed values (comma-separated, optional)',self.enum),('Maximum list items',self.maximum)]):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w');widget=ttk.Combobox(frame,textvariable=var,values=TYPES,state='readonly') if var is self.kind else ttk.Entry(frame,textvariable=var);widget.grid(row=row,column=1,sticky='ew',pady=3)
        for col,(label,var) in enumerate([('Require quotes',self.evidence),('Generated suggestion',self.generated),('Auto accept',self.accept),('Needs scope',self.scoped)]):ttk.Checkbutton(frame,text=label,variable=var).grid(row=4,column=col,sticky='w')
        ttk.Label(self.window,text='Instructions for this field').pack(anchor='w',padx=12);self.prompt=ScrolledText(self.window,height=5,wrap='word');self.prompt.pack(fill='both',expand=True,padx=12)
        bar=ttk.Frame(self.window,padding=12);bar.pack(fill='x')
        for label,command in [('Add / update field',self.update),('Remove selected',self.remove),('Save profile as…',self.save)]:ttk.Button(bar,text=label,command=command).pack(side='left',padx=4)
        self.draw();self.window.protocol('WM_DELETE_WINDOW',self.close)
        for var in (self.name,self.field_name,self.kind,self.enum,self.maximum,self.evidence,self.generated,self.accept,self.scoped):var.trace_add('write',self.changed)
        for widget in (self.instructions,self.prompt):widget.edit_modified(False);widget.bind('<<Modified>>',self.changed)

    def changed(self,*args):
        if self.loading:return
        event=args[0] if args and hasattr(args[0],'widget') else None
        if event:
            if not event.widget.edit_modified():return
            event.widget.edit_modified(False)
        self.dirty=True

    def close(self):
        if self.dirty and not messagebox.askyesno('Unsaved profile','Discard unsaved field/profile changes?',parent=self.window):return False
        self.window.destroy();return True

    def draw(self):
        self.fields.delete(0,'end')
        for field in self.profile['fields']:self.fields.insert('end',field['name']+' · '+field['type'])

    def select(self,event=None):
        selected=self.fields.curselection()
        if not selected:return
        self.loading=True
        field=self.profile['fields'][selected[0]];self.field_name.set(field['name']);self.kind.set(field['type']);self.enum.set(', '.join(field.get('enum',[])));self.maximum.set(str(field.get('max_items',12)))
        for key,var in [('evidence',self.evidence),('generated',self.generated),('auto_accept',self.accept),('scoped',self.scoped)]:var.set(field.get(key,False))
        self.prompt.delete('1.0','end');self.prompt.insert('end',field.get('prompt',''));self.prompt.edit_modified(False);self.loading=False

    def update(self):
        try:
            field={'name':self.field_name.get().strip(),'type':self.kind.get(),'prompt':self.prompt.get('1.0','end-1c'),'evidence':self.evidence.get(),'generated':self.generated.get(),'auto_accept':self.accept.get(),'scoped':self.scoped.get(),'max_items':int(self.maximum.get())}
            if self.enum.get().strip():field['enum']=[v.strip() for v in self.enum.get().split(',') if v.strip()]
            validate_profile({'name':'field-check','fields':[field]})
            index=next((i for i,f in enumerate(self.profile['fields']) if f['name']==field['name']),None)
            if index is None:self.profile['fields'].append(field)
            else:self.profile['fields'][index]=field
            self.dirty=True;self.draw();return True
        except ValueError as error:messagebox.showerror('Field definition',str(error),parent=self.window);return False

    def remove(self):
        selected=self.fields.curselection()
        if selected:self.dirty=True;self.profile['fields'].pop(selected[0]);self.field_name.set('');self.draw()

    def save(self):
        try:
            if self.field_name.get().strip() and not self.update():return
            profile=validate_profile({**self.profile,'name':self.name.get().strip(),'instructions':self.instructions.get('1.0','end-1c')})
            path=filedialog.asksaveasfilename(parent=self.window,initialdir=str(self.path.parent),initialfile=self.path.name,defaultextension='.json')
            if path:
                target=Path(path);stage=target.with_suffix('.tmp');stage.write_text(json.dumps(profile,ensure_ascii=False,indent=2),'utf-8');stage.replace(target);self.on_save(str(target));self.window.destroy()
        except (OSError,ValueError) as error:messagebox.showerror('Field profile',str(error),parent=self.window)
