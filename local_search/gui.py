"""Native Tk desktop UI. No browser, web server, Electron or GPU-rendered dashboard."""
import json
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter.scrolledtext import ScrolledText
from .store import Library
from .ingest import refresh
from .model import validate_cache


class App:
    def __init__(self,root,config_path):
        self.root=root;self.path=Path(config_path).resolve();self.busy=False;self.closing=False
        self.jobs=queue.Queue();self.results=queue.Queue();self.cancel=threading.Event();self.model=None;self.model_key=None
        root.title('Local Library Search');root.geometry('1100x780');root.minsize(860,650)
        self.status=tk.StringVar(value='Choose sources and a model, save settings, then refresh the index.');self.next_refresh=time.monotonic()
        self.vars={};self.sources=[];self.hits={};self.controls=[];self.next_offset=None;self.open_doc=None;self.next_char=None
        if self.path.exists():self.raw=json.loads(self.path.read_text('utf-8'))
        else:self.raw={'database':'data/library.sqlite3','sources':[],'model':{'context':12000,'output':2000,'gpu_layers':0,'cache_k':'f16','cache_v':'f16','thinking':False}}
        top=ttk.Frame(root,padding=10);top.pack(fill='x')
        ttk.Label(top,text='Local Library Search',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Label(top,text='Native desktop · local files · local model').pack(side='right')
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=10)
        self.search_tab=ttk.Frame(self.tabs,padding=12);self.setup_tab=ttk.Frame(self.tabs)
        self.setup_canvas=tk.Canvas(self.setup_tab,highlightthickness=0)
        scrollbar=ttk.Scrollbar(self.setup_tab,orient='vertical',command=self.setup_canvas.yview)
        scrollbar.pack(side='right',fill='y');self.setup_canvas.pack(side='left',fill='both',expand=True);self.setup_canvas.configure(yscrollcommand=scrollbar.set)
        self.setup=ttk.Frame(self.setup_canvas,padding=12);window=self.setup_canvas.create_window((0,0),window=self.setup,anchor='nw')
        self.setup.bind('<Configure>',lambda e:self.setup_canvas.configure(scrollregion=self.setup_canvas.bbox('all')))
        self.setup_canvas.bind('<Configure>',lambda e:self.setup_canvas.itemconfigure(window,width=e.width))
        self.tabs.add(self.search_tab,text='Search');self.tabs.add(self.setup_tab,text='Sources & model')
        self.build_setup();self.build_search()
        ttk.Label(root,textvariable=self.status,padding=10,wraplength=1050).pack(fill='x')
        if not self.path.exists():self.tabs.select(self.setup_tab)
        threading.Thread(target=self.worker,daemon=True).start();root.after(80,self.poll);root.after(1000,self.auto_refresh);root.protocol('WM_DELETE_WINDOW',self.close)

    def button(self,parent,text,command,**pack):
        widget=ttk.Button(parent,text=text,command=command);widget.pack(**pack);self.controls.append(widget);return widget

    def entry(self,parent,row,label,key,value,browse=None):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=4)
        var=tk.StringVar(value=str(value));self.vars[key]=var
        ttk.Entry(parent,textvariable=var).grid(row=row,column=1,sticky='ew',padx=8)
        if browse:
            widget=ttk.Button(parent,text='Browse…',command=lambda:self.choose(key,browse));widget.grid(row=row,column=2);self.controls.append(widget)

    def choose(self,key,kind):
        value=filedialog.askdirectory(parent=self.root) if kind=='dir' else filedialog.asksaveasfilename(parent=self.root,defaultextension='.sqlite3') if kind=='save' else filedialog.askopenfilename(parent=self.root,filetypes=[('GGUF model','*.gguf'),('All files','*')])
        if value:self.vars[key].set(value)

    def build_setup(self):
        ttk.Label(self.setup,text=f'Settings: {self.path}',wraplength=980).pack(anchor='w')
        box=ttk.Frame(self.setup);box.pack(fill='x',pady=4);box.columnconfigure(1,weight=1)
        self.entry(box,0,'Library database','database',self.raw.get('database','data/library.sqlite3'),'save')
        self.entry(box,1,'Auto-refresh seconds (0 = manual)','refresh_seconds',self.raw.get('refresh_seconds',0))
        self.source_tree=ttk.Treeview(self.setup,columns=('type','path','snapshot'),show='headings',height=4)
        for name,label,width in [('type','Type',90),('path','Source path',650),('snapshot','Closed snapshot',120)]:
            self.source_tree.heading(name,text=label);self.source_tree.column(name,width=width)
        self.source_tree.pack(fill='x',pady=5)
        self.sources=[dict(s) for s in self.raw.get('sources',[])];self.draw_sources()
        row=ttk.Frame(self.setup);row.pack(fill='x')
        self.button(row,'Add PDF folder…',lambda:self.add_source('folder'),side='left')
        self.button(row,'Add webclipper DB…',lambda:self.add_source('clipper'),side='left',padx=5)
        self.button(row,'Stop updating selected',self.remove_source,side='left')
        self.button(row,'Toggle closed snapshot',self.toggle_snapshot,side='left',padx=5)
        ttk.Label(self.setup,text='Closed snapshot is only for a stopped clipper. Live WAL/journal files are refused.',wraplength=980).pack(anchor='w',pady=4)
        model=ttk.LabelFrame(self.setup,text='Local GGUF model',padding=8);model.pack(fill='x',pady=8);model.columnconfigure(1,weight=1)
        cfg=self.raw.get('model',{})
        self.entry(model,0,'GGUF file','path',cfg.get('path',''),'file')
        self.entry(model,1,'PyTorch DLL folder (optional)','torch_lib',cfg.get('torch_lib',''),'dir')
        for row,label,key,default in [(2,'Total context tokens','context',12000),(3,'Output reserve, including thinking','output',2000),(4,'GPU layers (0 = CPU, -1 = all)','gpu_layers',0)]:self.entry(model,row,label,key,cfg.get(key,default))
        cache=ttk.Frame(model);cache.grid(row=5,column=0,columnspan=3,sticky='ew',pady=6)
        for label,key in [('K cache','cache_k'),('V cache','cache_v')]:
            ttk.Label(cache,text=label).pack(side='left',padx=(0,5));self.vars[key]=tk.StringVar(value=cfg.get(key,'f16'))
            ttk.Combobox(cache,textvariable=self.vars[key],values=('f16','q8_0','q4_0'),state='readonly',width=9).pack(side='left',padx=(0,18))
        for label,key,default in [('Flash Attention','flash_attention',False),('KV offload','kv_offload',True),('Thinking','thinking',False)]:
            self.vars[key]=tk.BooleanVar(value=cfg.get(key,default));ttk.Checkbutton(cache,text=label,variable=self.vars[key]).pack(side='left',padx=5)
        ttk.Label(model,text='F16 uses more cache memory; Q8/Q4 use less. Quantized V requires Flash Attention. Leave GPU headroom for your display; use fewer GPU layers or a smaller context.',wraplength=960).grid(row=6,column=0,columnspan=3,sticky='w',pady=5)
        actions=ttk.Frame(self.setup);actions.pack(fill='x',pady=4)
        self.button(actions,'Save settings',self.save,side='left')
        self.button(actions,'Refresh index',self.ingest,side='left',padx=5)
        self.button(actions,'Index status',self.index_status,side='left')
        self.button(actions,'Load & test GGUF',self.model_check,side='left',padx=5)
        self.button(actions,'Unload model',lambda:self.submit(self.unload),side='left')

    def draw_sources(self):
        self.source_tree.delete(*self.source_tree.get_children())
        for i,s in enumerate(self.sources):self.source_tree.insert('', 'end',iid=str(i),values=(s['type'],s['path'],'yes' if s.get('closed_snapshot') else 'no'))

    def add_source(self,kind):
        path=filedialog.askdirectory(parent=self.root) if kind=='folder' else filedialog.askopenfilename(parent=self.root,filetypes=[('SQLite database','*.db *.sqlite *.sqlite3'),('All files','*')])
        if not path:return
        if any(Path(s['path']).resolve()==Path(path).resolve() for s in self.sources):messagebox.showinfo('Already added','That source is already listed.');return
        n=1
        while any(s['name']==f'{kind}-{n}' for s in self.sources):n+=1
        self.sources.append({'name':f'{kind}-{n}','type':kind,'path':path});self.draw_sources()

    def remove_source(self):
        selected=self.source_tree.selection()
        if selected:self.sources.pop(int(selected[0]));self.draw_sources()

    def toggle_snapshot(self):
        selected=self.source_tree.selection()
        if selected:
            source=self.sources[int(selected[0])]
            if source['type']=='clipper':source['closed_snapshot']=not source.get('closed_snapshot',False);self.draw_sources()

    def save(self):
        try:
            model={k:self.vars[k].get() for k in ('path','torch_lib','cache_k','cache_v','flash_attention','kv_offload','thinking')}
            for key in ('context','output','gpu_layers'):model[key]=int(self.vars[key].get())
            if model['context']<1024 or not 64<=model['output']<model['context'] or model['gpu_layers']< -1:raise ValueError('Check context, output reserve and GPU layers.')
            validate_cache(model['cache_k'],model['cache_v'],model['flash_attention'])
            if not model['torch_lib']:model.pop('torch_lib')
            interval=int(self.vars['refresh_seconds'].get())
            if interval!=0 and interval<10:raise ValueError('Auto-refresh must be 0 or at least 10 seconds.')
            data={**self.raw,'database':self.vars['database'].get(),'refresh_seconds':interval,'sources':self.sources,'model':{**self.raw.get('model',{}),**model}}
            if not self.vars['torch_lib'].get():data['model'].pop('torch_lib',None)
            if not str(data['database']).strip():raise ValueError('Choose a library database.')
            destination=(self.path.parent/Path(data['database'])).resolve()
            if any((self.path.parent/Path(s['path'])).resolve()==destination for s in self.sources):raise ValueError('Library database must differ from source databases.')
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix(self.path.suffix+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8');temp.replace(self.path)
            self.raw=data;self.status.set('Settings saved. Model changes take effect on the next model operation.');return True
        except Exception as error:messagebox.showerror('Settings',str(error),parent=self.root);return False

    def cfg(self):
        from .__main__ import configuration
        return configuration(self.path)

    def build_search(self):
        self.question=tk.StringVar()
        ttk.Label(self.search_tab,text='Search terms or a question').pack(anchor='w')
        ttk.Entry(self.search_tab,textvariable=self.question).pack(fill='x',pady=5)
        row=ttk.Frame(self.search_tab);row.pack(fill='x')
        self.button(row,'Search FTS5',self.search,side='left')
        self.button(row,'Ask local model',self.question_model,side='left',padx=5)
        self.button(row,'Browse documents',self.browse,side='left')
        self.button(row,'More results',self.more_results,side='left',padx=5)
        self.stop=ttk.Button(row,text='Stop inference',command=self.cancel.set,state='disabled');self.stop.pack(side='right')
        filters=ttk.Frame(self.search_tab);filters.pack(fill='x',pady=6)
        self.date_field=tk.StringVar(value='added_at');self.after=tk.StringVar();self.before=tk.StringVar();self.sort=tk.StringVar(value='relevance')
        ttk.Label(filters,text='Date').pack(side='left');ttk.Combobox(filters,textvariable=self.date_field,values=('added_at','modified_at','published_at'),width=15,state='readonly').pack(side='left',padx=5)
        for label,var in [('After',self.after),('Before',self.before)]:ttk.Label(filters,text=label).pack(side='left');ttk.Entry(filters,textvariable=var,width=13).pack(side='left',padx=5)
        ttk.Combobox(filters,textvariable=self.sort,values=('relevance','newest'),width=12,state='readonly').pack(side='left')
        ttk.Label(self.search_tab,text='Dates use YYYY-MM-DD; added is capture/file creation, not publication. Double-click a result for surrounding text.').pack(anchor='w')
        self.tree=ttk.Treeview(self.search_tab,columns=('title','copies','date','preview'),show='headings',height=10)
        for name,label,width in [('title','Source',230),('copies','Locations',65),('date','Date',150),('preview','Text',500)]:self.tree.heading(name,text=label);self.tree.column(name,width=width)
        self.tree.pack(fill='both',expand=True,pady=7);self.tree.bind('<Double-1>',self.expand)
        pages=ttk.Frame(self.search_tab);pages.pack(fill='x')
        self.button(pages,'Read document from start',self.read_document,side='left')
        self.button(pages,'Next document page',lambda:self.read_document(next_page=True),side='left',padx=5)
        self.output=ScrolledText(self.search_tab,wrap='word',height=13,font=('Segoe UI',10));self.output.pack(fill='both',expand=True)

    def show(self,text):self.output.delete('1.0','end');self.output.insert('end',text)

    def submit(self,work,callback=None,inference=False):
        if self.busy:return
        self.busy=True;self.cancel.clear();self.status.set('Working…')
        for c in self.controls:c.configure(state='disabled')
        self.stop.configure(state='normal' if inference else 'disabled')
        self.jobs.put((work,callback))

    def worker(self):
        while True:
            work,callback=self.jobs.get()
            try:result=work();error=None
            except Exception as exc:result=None;error=str(exc)
            if self.closing:self.unload()
            self.results.put((result,error,callback))

    def poll(self):
        try:
            result,error,callback=self.results.get_nowait();self.busy=False
            if self.closing:self.root.destroy();return
            for c in self.controls:c.configure(state='normal')
            self.stop.configure(state='disabled')
            if error:self.status.set(error);messagebox.showerror('Operation failed',error,parent=self.root)
            else:
                self.status.set('Ready')
                if callback:callback(result)
        except queue.Empty:pass
        self.root.after(80,self.poll)

    def ingest(self,automatic=False):
        if not automatic and not self.save():return
        config=self.cfg()
        def work():
            with Library(config['database']) as lib:return refresh(lib,config)
        self.next_refresh=time.monotonic()+max(10,int(self.raw.get('refresh_seconds',0)))
        self.submit(work,(lambda r:self.status.set('Scheduled refresh finished. '+str(sum(x.get('errors',0)+('error' in x) for x in r))+' source/file errors.')) if automatic else self.display_report)

    def auto_refresh(self):
        if self.closing:return
        interval=int(self.raw.get('refresh_seconds',0))
        if interval>=10 and not self.busy and self.path.exists() and time.monotonic()>=self.next_refresh:
            self.ingest(automatic=True)
        self.root.after(1000,self.auto_refresh)

    def display_report(self,result):self.tabs.select(self.search_tab);self.show(json.dumps(result,ensure_ascii=False,indent=2))

    def index_status(self):
        if not self.save():return
        config=self.cfg()
        def work():
            with Library(config['database']) as lib:return lib.status()
        self.submit(work,self.display_report)

    def search(self,offset=0):
        if not self.save():return
        config=self.cfg();query=self.question.get();options=dict(after=self.after.get() or None,before=self.before.get() or None,date_field=self.date_field.get(),sort=self.sort.get(),limit=20,offset=offset)
        def work():
            with Library(config['database']) as lib:return lib.search(query,**options)
        def show(result):
            self.next_offset=result['next_offset'];self.result_mode='search'
            self.tree.delete(*self.tree.get_children());self.hits={}
            for i,hit in enumerate(result['results']):
                self.hits[str(i)]=hit;loc=hit['locations'][0]
                self.tree.insert('','end',iid=str(i),values=(loc['title'],hit['location_count'],hit['newest'] or 'unknown',hit['text'][:200]))
            self.show(f"{result['total_unique_passages']} unique passages. Showing up to 20.\n"+result['date_note'])
        self.submit(work,show)

    def more_results(self):
        if self.next_offset is not None:
            if self.result_mode=='search':self.search(self.next_offset)
            else:self.browse(self.next_offset)

    def browse(self,offset=0):
        if not self.save():return
        config=self.cfg()
        def work():
            with Library(config['database']) as lib:return lib.catalog(offset=offset,limit=50)
        def show(rows):
            self.tree.delete(*self.tree.get_children());self.hits={};self.result_mode='catalog';self.next_offset=offset+len(rows) if len(rows)==50 else None
            for i,row in enumerate(rows):
                self.hits[str(i)]=row
                self.tree.insert('','end',iid=str(i),values=(row['title'],1,row['added_at'] or 'unknown',row['status']+' · '+row['locator']))
            self.show('Select a document to read it. Skipped/error entries show why they were not indexed.')
        self.submit(work,show)

    def expand(self,event=None):
        selected=self.tree.selection()
        if not selected or self.busy:return
        hit=self.hits[selected[0]];config=self.cfg()
        if 'passage_id' not in hit:
            if hit.get('doc_id') is None:self.show(hit.get('detail','No indexed text'));return
            self.read_document();return
        def work():
            with Library(config['database']) as lib:return lib.context(hit['passage_id'],doc_id=hit['locations'][0]['doc_id'],radius_chars=2000)
        self.submit(work,lambda r:self.show(r['text']+'\n\nSources and dates:\n'+json.dumps(r['sources'],ensure_ascii=False,indent=2)))

    def read_document(self,next_page=False):
        if self.busy:return
        if next_page:
            if self.open_doc is None or self.next_char is None:return
            doc_id=self.open_doc;start=self.next_char
        else:
            selected=self.tree.selection()
            if not selected:return
            hit=self.hits[selected[0]];doc_id=hit.get('doc_id') or (hit['locations'][0]['doc_id'] if hit.get('locations') else None)
            if doc_id is None:self.show(hit.get('detail','No indexed text'));return
            start=0
        config=self.cfg()
        def work():
            with Library(config['database']) as lib:return lib.read(doc_id,start,8000)
        def show(result):
            self.open_doc=doc_id;self.next_char=result['next_start']
            self.show(result['text']+f"\n\nCharacters {result['start_char']}–{result['end_char']} of {result['total_chars']}."+(' End of indexed document.' if self.next_char is None else ' Use Next document page to continue.'))
        self.submit(work,show)

    def loaded_model(self,config):
        key=json.dumps(config['model'],sort_keys=True)
        if key!=self.model_key:
            self.unload()
            from .model import GGUF
            self.model=GGUF(**config['model']);self.model_key=key
        self.model.cancel_event=self.cancel;return self.model

    def unload(self):
        if self.model:self.model.close();self.model=None;self.model_key=None
        return {'model':'unloaded'}

    def model_check(self):
        if not self.save():return
        config=self.cfg()
        def work():
            model=self.loaded_model(config)
            if self.cancel.is_set():return {'status':'cancelled'}
            result=model.complete([{'role':'user','content':'Reply with READY.'}])
            return {'response':result['choices'][0]['text'],'usage':result.get('usage'),'DLL folders':model.dlls.paths}
        self.submit(work,self.display_report,inference=True)

    def question_model(self):
        if not self.save():return
        config=self.cfg();question=self.question.get().strip()
        if not question:return
        def work():
            from .agent import ask
            model=self.loaded_model(config)
            with Library(config['database']) as lib:result=ask(lib,model,question)
            trace=Path(config['database']).parent/'last-gui-search.json';trace.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
            return result
        self.submit(work,lambda r:self.show((r.get('answer') or r['status'])+'\n\nSource IDs: '+str(r.get('citations',[]))),inference=True)

    def close(self):
        self.closing=True;self.cancel.set()
        if self.busy:self.status.set('Closing after the current native operation stops…')
        else:self.submit(self.unload)


def launch(path):
    root=tk.Tk();App(root,path);root.mainloop()
