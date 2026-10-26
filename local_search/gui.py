"""Native Tk desktop UI. No browser, web server, Electron or GPU-rendered dashboard."""
import json
import queue
import threading
import time
import os
import webbrowser
import tempfile
from urllib.parse import urlsplit
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter.scrolledtext import ScrolledText
from .store import Library
from .ingest import refresh
from .model import validate_cache
from .timing import complete_timing,format_timing
from .progress import format_progress


def open_source(source):
    """Only destinations from the library registry, never a generated shell command."""
    locator=source['locator'];scheme=urlsplit(locator).scheme.lower()
    if scheme in ('http','https'):
        if not urlsplit(locator).netloc:raise ValueError('Invalid source URL')
        webbrowser.open(locator);return
    path=Path(locator)
    if not path.is_absolute() or path.suffix.lower()!='.pdf':raise ValueError('Source has no supported PDF or HTTP(S) destination')
    if not path.is_file():raise ValueError('Source file has moved or is unavailable: '+str(path))
    os.startfile(str(path))


class App:
    def __init__(self,root,config_path):
        self.root=root;self.path=Path(config_path).resolve();self.busy=False;self.closing=False
        self.jobs=queue.Queue();self.results=queue.Queue();self.cancel=threading.Event();self.model=None;self.model_key=None
        self.reranker=None;self.reranker_key=None
        self.events=queue.Queue(maxsize=256);self.library=None;self.library_key=None;self.tooltip=None
        self.trace_lock=threading.Lock();self.question_serial=0
        self.ingest_lock=threading.Lock();self.ingest_latest=None;self.ingesting=False
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
        value=filedialog.askdirectory(parent=self.root) if kind=='dir' else filedialog.asksaveasfilename(parent=self.root,defaultextension='.sqlite3') if kind=='save' else filedialog.askopenfilename(parent=self.root,filetypes=[('Prompt library','*.txt'),('All files','*')] if kind=='prompts' else [('GGUF model','*.gguf'),('All files','*')])
        if value:self.vars[key].set(value)

    def build_setup(self):
        ttk.Label(self.setup,text=f'Settings: {self.path}',wraplength=980).pack(anchor='w')
        box=ttk.Frame(self.setup);box.pack(fill='x',pady=4);box.columnconfigure(1,weight=1)
        self.entry(box,0,'Library database','database',self.raw.get('database','data/library.sqlite3'),'save')
        self.entry(box,1,'Auto-refresh seconds (0 = manual)','refresh_seconds',self.raw.get('refresh_seconds',0))
        self.entry(box,2,'Database cache MiB (1–1024, per connection)','cache_mb',self.raw.get('database_options',{}).get('cache_mb',32))
        self.entry(box,3,'Prompt library (blank = bundled prompts.txt)','prompts_file',self.raw.get('prompts_file',''),'prompts')
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
        ttk.Button(model,text='Use 64k total / 8k output reserve',command=lambda:(self.vars['context'].set('65536'),self.vars['output'].set('8192'))).grid(row=7,column=0,columnspan=3,sticky='w')
        cache=ttk.Frame(model);cache.grid(row=5,column=0,columnspan=3,sticky='ew',pady=6)
        for label,key in [('K cache','cache_k'),('V cache','cache_v')]:
            ttk.Label(cache,text=label).pack(side='left',padx=(0,5));self.vars[key]=tk.StringVar(value=cfg.get(key,'f16'))
            ttk.Combobox(cache,textvariable=self.vars[key],values=('f16','q8_0','q4_0'),state='readonly',width=9).pack(side='left',padx=(0,18))
        for label,key,default in [('Flash Attention','flash_attention',False),('KV offload','kv_offload',True),('Thinking','thinking',False)]:
            self.vars[key]=tk.BooleanVar(value=cfg.get(key,default));ttk.Checkbutton(cache,text=label,variable=self.vars[key]).pack(side='left',padx=5)
        ttk.Label(model,text='F16 uses more cache memory; Q8/Q4 use less. Quantized V requires Flash Attention. Leave GPU headroom for your display; use fewer GPU layers or a smaller context.',wraplength=960).grid(row=6,column=0,columnspan=3,sticky='w',pady=5)
        rank=ttk.LabelFrame(self.setup,text='Optional reranker — model searches only',padding=8);rank.pack(fill='x',pady=8);rank.columnconfigure(1,weight=1)
        cfg=self.raw.get('reranker',{})
        self.vars['rerank_enabled']=tk.BooleanVar(value=cfg.get('enabled',False))
        ttk.Checkbutton(rank,text='Enable reranking',variable=self.vars['rerank_enabled']).grid(row=0,column=0,sticky='w')
        self.vars['rerank_device']=tk.StringVar(value=cfg.get('device','cpu'))
        ttk.Combobox(rank,textvariable=self.vars['rerank_device'],values=('cpu','cuda:0','cuda:1'),state='readonly',width=12).grid(row=0,column=1,sticky='w')
        self.entry(rank,1,'Local CrossEncoder model folder','rerank_model_dir',cfg.get('model_dir',''),'dir')
        for row,label,key,default in [(2,'Candidates to rerank (1–1000)','candidates',40),(3,'Maximum returned per search (1–20)','top_k',5),
                                      (4,'Batch size (1–64)','batch_size',8),(5,'CPU threads (1–32)','threads',2),
                                      (6,'Pair token budget','max_pair_tokens',512),(7,'Scoring timeout seconds','timeout_seconds',5),
                                      (8,'Startup timeout seconds','startup_seconds',60)]:
            self.entry(rank,row,label,'rerank_'+key,cfg.get(key,default))
        ttk.Label(rank,text='Ranks FTS passages against your question. CPU is default; CUDA requires GPU-enabled PyTorch and shares VRAM with the GGUF and display. Larger pools cost more time. Direct FTS searches are unchanged.',wraplength=960).grid(row=9,column=0,columnspan=3,sticky='w',pady=5)
        actions=ttk.Frame(self.setup);actions.pack(fill='x',pady=4)
        self.button(actions,'Save settings',self.save,side='left')
        self.button(actions,'Refresh index',self.ingest,side='left',padx=5)
        self.button(actions,'Index status',self.index_status,side='left')
        self.button(actions,'Load & test GGUF',self.model_check,side='left',padx=5)
        self.button(actions,'Unload model',lambda:self.submit(self.unload),side='left')
        self.button(actions,'Unload / reset reranker',lambda:self.submit(self.unload_reranker),side='left',padx=5)

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
            cache_mb=int(self.vars['cache_mb'].get())
            if not 1<=cache_mb<=1024:raise ValueError('Database cache must be 1..1024 MiB per connection.')
            if interval!=0 and interval<10:raise ValueError('Auto-refresh must be 0 or at least 10 seconds.')
            data={**self.raw,'database':self.vars['database'].get(),'refresh_seconds':interval,'sources':self.sources,'model':{**self.raw.get('model',{}),**model}}
            data['database_options']={'cache_mb':cache_mb}
            from .rerank import RerankConfig
            from dataclasses import asdict
            rank={'enabled':self.vars['rerank_enabled'].get(),'device':self.vars['rerank_device'].get(),'model_dir':self.vars['rerank_model_dir'].get().strip()}
            for key in ('candidates','top_k','batch_size','threads','max_pair_tokens'):rank[key]=int(self.vars['rerank_'+key].get())
            for key in ('timeout_seconds','startup_seconds'):rank[key]=float(self.vars['rerank_'+key].get())
            data['reranker']=asdict(RerankConfig(**rank))
            if self.vars['prompts_file'].get().strip():data['prompts_file']=self.vars['prompts_file'].get().strip()
            else:data.pop('prompts_file',None)
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
        ttk.Label(self.search_tab,text='Ingestion progress').pack(anchor='w')
        self.ingest_bar=ttk.Progressbar(self.search_tab,mode='determinate',maximum=1,value=0)
        self.ingest_bar.pack(fill='x',pady=(0,5))
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
        self.exact_count=tk.BooleanVar(value=False)
        ttk.Checkbutton(filters,text='Exact total (may be slower)',variable=self.exact_count).pack(side='left',padx=8)
        ttk.Label(self.search_tab,text='Dates use YYYY-MM-DD; added is capture/file creation, not publication. Double-click a result for surrounding text.').pack(anchor='w')
        self.tree=ttk.Treeview(self.search_tab,columns=('title','copies','date','preview'),show='headings',height=10)
        for name,label,width in [('title','Source',230),('copies','Locations',65),('date','Date',150),('preview','Text',500)]:self.tree.heading(name,text=label);self.tree.column(name,width=width)
        self.tree.pack(fill='both',expand=True,pady=7);self.tree.bind('<Double-1>',self.expand)
        pages=ttk.Frame(self.search_tab);pages.pack(fill='x')
        self.button(pages,'Read document from start',self.read_document,side='left')
        self.button(pages,'Next document page',lambda:self.read_document(next_page=True),side='left',padx=5)
        self.output=ScrolledText(self.search_tab,wrap='word',height=13,font=('Segoe UI',10));self.output.pack(fill='both',expand=True)
        self.live_visible=tk.BooleanVar(value=False)
        self.live=ScrolledText(self.search_tab,wrap='word',height=5,state='disabled')
        ttk.Checkbutton(self.search_tab,text='Show live model output: thinking / action JSON (unvalidated)',variable=self.live_visible,command=lambda:self.live.pack(fill='x') if self.live_visible.get() else self.live.pack_forget()).pack(anchor='w')
        self.metrics=tk.StringVar();ttk.Label(self.search_tab,textvariable=self.metrics,wraplength=1000).pack(anchor='w')
        self.timing_visible=tk.BooleanVar(value=False)
        self.timing_details=ScrolledText(self.search_tab,wrap='word',height=8,state='disabled')
        ttk.Checkbutton(self.search_tab,text='Show question timing breakdown',variable=self.timing_visible,command=lambda:self.timing_details.pack(fill='x') if self.timing_visible.get() else self.timing_details.pack_forget()).pack(anchor='w')

    def show(self,text):
        self.hide_hint()
        for tag in self.output.tag_names():
            if tag.startswith('source-'):self.output.tag_delete(tag)
        self.output.delete('1.0','end');self.output.insert('end',text)

    def hide_hint(self,event=None):
        if self.tooltip:self.tooltip.destroy();self.tooltip=None

    def hint(self,event,text):
        self.hide_hint();self.tooltip=tk.Toplevel(self.root);self.tooltip.overrideredirect(True)
        self.tooltip.geometry(f'+{event.x_root+12}+{event.y_root+16}')
        ttk.Label(self.tooltip,text=text,padding=8,wraplength=850,relief='solid').pack()

    def source_links(self,sources):
        if not sources:return
        self.output.insert('end','\n\nSources (hover for title, path and dates):\n')
        displayed=set()
        for source in sources:
            if source['id'] in displayed:continue
            displayed.add(source['id'])
            tag='source-'+str(source['id']);label=f"[{source['id']}] {source['title']}"
            self.output.insert('end',label+'\n',tag);self.output.tag_configure(tag,foreground='#145da0',underline=True)
            detail='\n'.join([source['title'],source['locator']]+[f'{k}: {source[k]}' for k in ('added_at','modified_at','published_at') if source.get(k)])
            self.output.tag_bind(tag,'<Enter>',lambda e,t=detail:(self.output.configure(cursor='hand2'),self.hint(e,t)))
            self.output.tag_bind(tag,'<Leave>',lambda e:(self.output.configure(cursor='xterm'),self.hide_hint()))
            self.output.tag_bind(tag,'<Button-1>',lambda e,s=dict(source):self.open_link(s))

    def open_link(self,source):
        self.hide_hint()
        try:open_source(source)
        except Exception as error:messagebox.showerror('Open source',str(error),parent=self.root)

    def get_library(self,config):
        key=(config['database'],int(config.get('database_options',{}).get('cache_mb',32)))
        if key!=self.library_key:
            if self.library:self.library.close()
            self.library=None;self.library_key=None
            self.library=Library(key[0],cache_mb=key[1]);self.library_key=key
        return self.library

    def emit(self,event):self.events.put(event)

    def ingest_progress(self,event):
        # Coalesce fast file updates instead of filling the inference event queue.
        with self.ingest_lock:self.ingest_latest=(event,time.monotonic())

    def display_ingest_progress(self):
        with self.ingest_lock:latest=self.ingest_latest
        if not latest:return
        original,received=latest;event=dict(original)
        event['elapsed_seconds']+=time.monotonic()-received
        prefix='' if event['collection']=='library' else f"Source {event.get('source_number',1)}/{event.get('source_count',1)} · "
        self.status.set(prefix+format_progress(event))
        if event.get('total') is not None:
            self.ingest_bar.stop();self.ingest_bar.configure(mode='determinate',maximum=max(1,event['total']),value=event['processed'])
        elif str(self.ingest_bar['mode'])!='indeterminate':
            self.ingest_bar.configure(mode='indeterminate');self.ingest_bar.start(80)

    def submit(self,work,callback=None,inference=False):
        if self.busy:return
        self.busy=True;self.cancel.clear();self.status.set('Working…')
        self.metrics.set('')
        self.timing_details.configure(state='normal');self.timing_details.delete('1.0','end');self.timing_details.configure(state='disabled')
        for c in self.controls:c.configure(state='disabled')
        self.stop.configure(state='normal' if inference else 'disabled')
        self.jobs.put((work,callback))
        self.operation_started=time.perf_counter()

    def worker(self):
        while True:
            work,callback=self.jobs.get()
            try:result=work();error=None
            except Exception as exc:result=None;error=str(exc)
            if self.closing:
                self.unload()
                if self.library:self.library.close();self.library=None
            self.results.put((result,error,callback))

    def poll(self):
        if self.ingesting:self.display_ingest_progress()
        # A fixed-size queue plus batched UI writes bounds streaming overhead and memory.
        text=[]
        for _ in range(256):
            try:event=self.events.get_nowait()
            except queue.Empty:break
            if event['type']=='text':text.append(event['text'])
            elif event['type']=='round':text.append(f"\n--- Model round {event['round']} ---\n")
            elif event['type']=='phase':self.status.set(event['text'])
        if text:
            self.live.configure(state='normal');self.live.insert('end',''.join(text))
            if int(self.live.index('end-1c').split('.')[0])>1000:self.live.delete('1.0','end-1000lines')
            if len(self.live.get('1.0','end-1c'))>32000:self.live.delete('1.0','end-32000c')
            self.live.see('end');self.live.configure(state='disabled')
        try:
            result,error,callback=self.results.get_nowait();self.busy=False
            if self.ingesting:
                self.ingesting=False;self.ingest_bar.stop()
            if self.closing:self.root.destroy();return
            for c in self.controls:c.configure(state='normal')
            self.stop.configure(state='disabled')
            if error:self.status.set(error);messagebox.showerror('Operation failed',error,parent=self.root)
            else:
                self.status.set('Ready')
                if callback:callback(result)
                elapsed=time.perf_counter()-self.operation_started
                if not isinstance(result,dict) or 'gui_timing' not in result:self.metrics.set(self.metrics.get()+f' | Displayed in {elapsed:.2f}s')
        except queue.Empty:pass
        self.root.after(80,self.poll)

    def ingest(self,automatic=False):
        if self.busy:return
        if not automatic and not self.save():return
        if not automatic:self.tabs.select(self.search_tab)
        config=self.cfg()
        def work():
            self.ingest_progress({'type':'ingest_progress','collection':'library','phase':'Opening database','current':config['database'],
                                  'processed':0,'total':None,'stats':{},'elapsed_seconds':0,'source_count':len(config['sources'])})
            return refresh(self.get_library(config),config,on_progress=self.ingest_progress)
        self.next_refresh=time.monotonic()+max(10,int(self.raw.get('refresh_seconds',0)))
        self.ingesting=True
        with self.ingest_lock:self.ingest_latest=None
        self.ingest_bar.configure(mode='indeterminate');self.ingest_bar.start(80)
        def show(reports):
            count=sum(r.get('indexed',0)+r.get('skipped',0)+r.get('errors',0) for r in reports)
            errors=sum(r.get('errors',0)+len(r.get('scan_errors',[]))+('error' in r) for r in reports)
            skipped=sum(r.get('skipped',0) for r in reports);cached=sum(r.get('cached',0) for r in reports)
            self.ingest_bar.configure(mode='determinate',maximum=1,value=1)
            self.status.set(f'Refresh finished: {count} items processed, {cached} cached, {skipped} skipped, {errors} errors. Log: '+str(Path(config['database']).parent/'ingestion.log'))
            if not automatic:self.display_report(reports)
        self.submit(work,show)

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
            lib=self.get_library(config)
            return {**lib.status(),'database_bytes':lib.path.stat().st_size,'cache_size_kib':-lib.db.execute('PRAGMA cache_size').fetchone()[0]}
        self.submit(work,self.display_report)

    def search(self,offset=0):
        if not self.save():return
        config=self.cfg();query=self.question.get();options=dict(after=self.after.get() or None,before=self.before.get() or None,date_field=self.date_field.get(),sort=self.sort.get(),limit=20,offset=offset,exact_count=self.exact_count.get())
        submitted=time.perf_counter()
        def work():
            started=time.perf_counter();lib=self.get_library(config);opened=time.perf_counter()
            result=lib.search(query,**options)
            result['timings'].update(queue_seconds=started-submitted,connection_seconds=opened-started)
            return result
        def show(result):
            render_started=time.perf_counter()
            self.next_offset=result['next_offset'];self.result_mode='search'
            self.tree.delete(*self.tree.get_children());self.hits={}
            for i,hit in enumerate(result['results']):
                self.hits[str(i)]=hit;loc=hit['locations'][0]
                self.tree.insert('','end',iid=str(i),values=(loc['title'],hit['location_count'],hit['newest'] or 'unknown',hit['text'][:200]))
            total=result['total_unique_passages'];self.show((f'{total} unique passages.' if total is not None else 'Exact total not requested.')+' Showing up to 20.\n'+result['date_note'])
            self.source_links([dict(id=h['locations'][0]['source_id'],**{k:v for k,v in h['locations'][0].items() if k!='source_id'}) for h in result['results']])
            result['timings']['render_seconds']=time.perf_counter()-render_started
            self.metrics.set('Search: '+', '.join(f"{k.removesuffix('_seconds')} {v*1000:.1f}ms" for k,v in result['timings'].items()))
        self.submit(work,show)

    def more_results(self):
        if self.next_offset is not None:
            if self.result_mode=='search':self.search(self.next_offset)
            else:self.browse(self.next_offset)

    def browse(self,offset=0):
        if not self.save():return
        config=self.cfg()
        def work():
            return self.get_library(config).catalog(offset=offset,limit=50)
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
            return self.get_library(config).context(hit['passage_id'],doc_id=hit['locations'][0]['doc_id'],radius_chars=2000)
        def show(result):self.show(result['text']);self.source_links(result['sources'])
        self.submit(work,show)

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
            return self.get_library(config).read(doc_id,start,8000)
        def show(result):
            self.open_doc=doc_id;self.next_char=result['next_start']
            self.show(result['text']+f"\n\nCharacters {result['start_char']}–{result['end_char']} of {result['total_chars']}."+(' End of indexed document.' if self.next_char is None else ' Use Next document page to continue.'))
            self.source_links(result['sources'])
        self.submit(work,show)

    def loaded_model(self,config):
        key=json.dumps(config['model'],sort_keys=True)
        if key!=self.model_key:
            self.unload()
            from .model import GGUF
            self.emit({'type':'phase','text':'Loading model'})
            started=time.perf_counter();self.model=GGUF(**config['model']);self.model_key=key
            self.last_load_seconds=time.perf_counter()-started
        else:self.last_load_seconds=0
        self.model.cancel_event=self.cancel;return self.model

    def unload(self):
        if self.model:self.model.close();self.model=None;self.model_key=None
        self.unload_reranker()
        return {'model':'unloaded'}

    def unload_reranker(self):
        if self.reranker:self.reranker.close()
        self.reranker=None;self.reranker_key=None
        return {'reranker':'unloaded; next request can retry'}

    def loaded_reranker(self,config):
        from .rerank import RerankConfig,Reranker
        options=RerankConfig(**config.get('reranker',{}));key=json.dumps(config.get('reranker',{}),sort_keys=True)
        if not options.enabled:self.unload_reranker();return None
        if key!=self.reranker_key:
            self.unload_reranker();self.reranker=Reranker(options);self.reranker_key=key
        return self.reranker

    def model_check(self):
        if not self.save():return
        config=self.cfg()
        def work():
            model=self.loaded_model(config)
            if self.cancel.is_set():return {'status':'cancelled'}
            result=model.complete([{'role':'user','content':'Reply with READY.'}],on_event=self.emit)
            return {'response':result['choices'][0]['text'],'usage':result.get('usage'),'timings':result.get('timings'),'load_seconds':self.last_load_seconds,'DLL folders':model.dlls.paths}
        self.submit(work,self.display_report,inference=True)

    def question_model(self):
        clicked=time.perf_counter()
        if not self.save():return
        config=self.cfg();question=self.question.get().strip()
        if not question:return
        self.question_serial+=1;question_serial=self.question_serial
        self.live.configure(state='normal');self.live.delete('1.0','end');self.live.configure(state='disabled')
        submitted=time.perf_counter();clock={}
        def work():
            from .agent import ask
            clock['worker_started']=time.perf_counter()
            started=time.perf_counter()
            model=self.loaded_model(config)
            clock['load_seconds']=time.perf_counter()-started
            started=time.perf_counter();library=self.get_library(config)
            clock['library_seconds']=time.perf_counter()-started
            result=ask(library,model,question,on_event=self.emit,prompts_file=config.get('prompts_file'),reranker=self.loaded_reranker(config))
            result['timings']['load_seconds']=clock['load_seconds']
            trace=Path(config['database']).parent/'last-gui-search.json'
            with self.trace_lock:trace.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
            clock['worker_finished']=time.perf_counter()
            return result
        def show(result):
            render_started=time.perf_counter()
            self.show(result.get('answer') or result['status']);self.source_links(result.get('sources',[]))
            self.output.update_idletasks()
            completed=time.perf_counter()
            timing=complete_timing(result,clicked,submitted,clock['worker_started'],clock['worker_finished'],render_started,completed,clock['load_seconds'],clock['library_seconds'])
            result['gui_timing']=timing
            self.timing_details.configure(state='normal');self.timing_details.delete('1.0','end');self.timing_details.insert('end',format_timing(timing));self.timing_details.configure(state='disabled')
            checks=result.get('context_checks',[])
            rates=[c['timings']['approx_decode_tokens_per_second'] for c in checks if c.get('timings',{}).get('approx_decode_tokens_per_second') is not None]
            label='Click → final answer complete' if result['status']=='answered' else 'Click → stopped ('+result['status']+')'
            self.metrics.set(f"{label}: {timing['click_to_complete_seconds']:.2f}s | FTS searches {result['timings']['fts_search_seconds']:.2f}s | {timing['model_rounds']} model rounds"+(f' | Last round ~{rates[-1]:.1f} tok/s' if rates else '')+' | See timing breakdown')
            if 'reranking' in result:
                applied=sum(t['rerank']['applied'] for t in result['reranking']);fallbacks=[t['rerank']['reason'] for t in result['reranking'] if t['rerank']['reason']]
                self.metrics.set(self.metrics.get()+f" | Rerank {result['timings'].get('rerank_seconds',0):.2f}s ({applied} applied)"+(' | Fallback: '+', '.join(dict.fromkeys(fallbacks)) if fallbacks else ''))
            # Save final GUI measurements after the answer is displayed. This background
            # persistence is deliberately outside click-to-answer completion time.
            trace=Path(config['database']).parent/'last-gui-search.json'
            def persist():
                temporary=None
                try:
                    with self.trace_lock:
                        if question_serial!=self.question_serial:return
                        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=trace.parent,prefix='timing-',suffix='.tmp',delete=False) as handle:
                            temporary=Path(handle.name);json.dump(result,handle,ensure_ascii=False,indent=2)
                        temporary.replace(trace)
                except OSError:
                    if temporary:temporary.unlink(missing_ok=True)
            threading.Thread(target=persist,daemon=True).start()
        self.submit(work,show,inference=True)

    def close(self):
        self.closing=True;self.cancel.set()
        if self.busy:self.status.set('Closing after the current native operation stops…')
        else:self.submit(self.unload)


def launch(path):
    root=tk.Tk();App(root,path);root.mainloop()
