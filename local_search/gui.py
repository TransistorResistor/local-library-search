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


def open_source(source,options=None):
    """Only destinations from the library registry, never a generated shell command."""
    locator=source['locator'];scheme=urlsplit(locator).scheme.lower()
    if scheme in ('http','https'):
        if not urlsplit(locator).netloc:raise ValueError('Invalid source URL')
        webbrowser.open(locator);return
    path=Path(locator)
    if not path.is_absolute() or path.suffix.lower()!='.pdf':raise ValueError('Source has no supported PDF or HTTP(S) destination')
    if not path.is_file():raise ValueError('Source file has moved or is unavailable: '+str(path))
    if options:
        from .pdf_viewer import open_page
        reason=open_page(path,source.get('page'),options)
        if reason is None:return {'opened_at_page':source['page']}
        os.startfile(str(path));return {'fallback':reason}
    os.startfile(str(path))


class App:
    def __init__(self,root,config_path):
        self.root=root;self.path=Path(config_path).resolve();self.busy=False;self.closing=False
        self.jobs=queue.Queue();self.results=queue.Queue();self.cancel=threading.Event();self.model=None;self.model_key=None
        self.reranker=None;self.reranker_key=None
        self.manager=None;self.management_key=None;self.manual_searcher=None;self.active_steering=None
        self.model_lease=None
        self.next_sync_check=time.monotonic()+15
        from .display_stream import DisplayStream
        self.display_stream=DisplayStream()
        self.events=queue.Queue(maxsize=256);self.library=None;self.library_key=None;self.tooltip=None
        self.trace_lock=threading.Lock();self.question_serial=0
        self.ingest_lock=threading.Lock();self.ingest_latest=None;self.ingesting=False
        root.title('Local Library Search');root.geometry('1100x780');root.minsize(860,650)
        self.status=tk.StringVar(value='Choose sources and a model, save settings, then refresh the index.');self.next_refresh=time.monotonic()
        self.vars={};self.sources=[];self.hits={};self.controls=[];self.next_offset=None;self.open_doc=None;self.next_char=None
        if self.path.exists():self.raw=json.loads(self.path.read_text('utf-8'))
        else:self.raw={'database':'data/library.sqlite3','sources':[],'model':{'context':12000,'output':2000,'gpu_layers':0,'cache_k':'f16','cache_v':'f16','thinking':False}}
        style=ttk.Style(root);style.configure('TNotebook.Tab',padding=(12,6))
        top=ttk.Frame(root,padding=(14,10));top.pack(fill='x')
        ttk.Label(top,text='Local Library Search',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Label(top,text='Native desktop · local files · local model').pack(side='right')
        self.status_label=ttk.Label(root,textvariable=self.status,padding=(14,8),wraplength=1050)
        self.status_label.pack(side='bottom',fill='x')
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=12,pady=(0,4))
        self.search_tab=ttk.Frame(self.tabs,padding=10);self.setup_tab=ttk.Frame(self.tabs)
        self.setup_actions=ttk.Frame(self.setup_tab,padding=(12,8));self.setup_actions.pack(side='bottom',fill='x')
        self.setup_canvas=tk.Canvas(self.setup_tab,highlightthickness=0)
        scrollbar=ttk.Scrollbar(self.setup_tab,orient='vertical',command=self.setup_canvas.yview)
        scrollbar.pack(side='right',fill='y');self.setup_canvas.pack(side='left',fill='both',expand=True);self.setup_canvas.configure(yscrollcommand=scrollbar.set)
        self.setup=ttk.Frame(self.setup_canvas,padding=12);window=self.setup_canvas.create_window((0,0),window=self.setup,anchor='nw')
        self.setup.bind('<Configure>',lambda e:self.setup_canvas.configure(scrollregion=self.setup_canvas.bbox('all')))
        self.setup_canvas.bind('<Configure>',lambda e:self.setup_canvas.itemconfigure(window,width=e.width))
        self.tabs.add(self.search_tab,text='Search');self.tabs.add(self.setup_tab,text='Settings')
        self.build_search();self.build_setup()
        root.bind('<Configure>',self.resize_labels)
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
        value=filedialog.askdirectory(parent=self.root) if kind=='dir' else filedialog.asksaveasfilename(parent=self.root,defaultextension='.sqlite3') if kind=='save' else filedialog.askopenfilename(parent=self.root,filetypes=[('Prompt library','*.txt'),('All files','*')] if kind=='prompts' else [('Executable','*.exe'),('All files','*')] if kind=='exe' else [('GGUF model','*.gguf'),('All files','*')])
        if value:self.vars[key].set(value)

    def build_setup(self):
        self.settings_pages=ttk.Notebook(self.setup);self.settings_pages.pack(fill='x')
        library_page=ttk.Frame(self.settings_pages,padding=12);model_page=ttk.Frame(self.settings_pages,padding=12)
        rank_page=ttk.Frame(self.settings_pages,padding=12);preferences=ttk.Frame(self.settings_pages,padding=12)
        for page,label in ((library_page,'Library & sources'),(model_page,'Local model'),(rank_page,'Reranker'),(preferences,'Preferences')):
            self.settings_pages.add(page,text=label)
        ttk.Label(library_page,text='Choose where your index lives and which folders or clippers to include.',wraplength=760).pack(anchor='w',pady=(0,8))
        box=ttk.Frame(library_page);box.pack(fill='x',pady=4);box.columnconfigure(1,weight=1)
        self.entry(box,0,'Library database','database',self.raw.get('database','data/library.sqlite3'),'save')
        self.entry(box,1,'Auto-refresh seconds (0 = manual)','refresh_seconds',self.raw.get('refresh_seconds',0))
        sync=self.raw.get('library_sync',{})
        self.vars['managed_library']=tk.BooleanVar(value=sync.get('enabled',False))
        self.vars['auto_sync']=tk.BooleanVar(value=sync.get('auto_sync',False))
        ttk.Checkbutton(box,text='Use AppData working copy; database above is the published copy',variable=self.vars['managed_library']).grid(row=2,column=0,columnspan=3,sticky='w')
        ttk.Checkbutton(box,text='Automatically sync changes back (single user)',variable=self.vars['auto_sync']).grid(row=3,column=0,columnspan=3,sticky='w')
        sync_actions=ttk.Frame(library_page);sync_actions.pack(fill='x')
        self.button(sync_actions,'Library / sync status',self.sync_status,side='left')
        self.button(sync_actions,'Sync now',self.sync_now,side='left',padx=5)
        self.button(sync_actions,'Load published copy',self.load_published,side='left',padx=5)
        ttk.Label(library_page,text='Network libraries use a local AppData copy. Existing remote libraries must be closed exports. Conflicting changes are retained for review.',wraplength=760).pack(anchor='w',pady=4)
        prefs=ttk.Frame(preferences);prefs.pack(fill='x');prefs.columnconfigure(1,weight=1)
        self.entry(prefs,0,'Notebook token limit (64–32768)','note_tokens',self.raw.get('agent_options',{}).get('note_tokens',6000))
        self.entry(prefs,1,'Prompt library (blank = bundled prompts.txt)','prompts_file',self.raw.get('prompts_file',''),'prompts')
        self.entry(prefs,2,'Database cache MiB (1–1024, per connection)','cache_mb',self.raw.get('database_options',{}).get('cache_mb',32))
        viewer=self.raw.get('pdf_viewer',{})
        self.vars['pdf_page_enabled']=tk.BooleanVar(value=viewer.get('enabled',False))
        self.vars['pdf_viewer']=tk.StringVar(value=viewer.get('viewer','acrobat'))
        ttk.Checkbutton(prefs,text='Open PDFs at known cited page',variable=self.vars['pdf_page_enabled']).grid(row=3,column=0,sticky='w')
        ttk.Combobox(prefs,textvariable=self.vars['pdf_viewer'],values=('acrobat','chrome'),state='readonly').grid(row=3,column=1,sticky='ew',padx=8)
        self.entry(prefs,4,'Viewer executable (blank = auto-detect)','pdf_executable',viewer.get('executable',''),'exe')
        ttk.Label(preferences,text='Notebook space is part of the model context. Prompt edits apply on the next question.',wraplength=760).pack(anchor='w',pady=12)
        ttk.Label(preferences,text=f'Settings file: {self.path}',wraplength=760).pack(anchor='w')
        source_box=ttk.Frame(library_page);source_box.pack(fill='both',expand=True,pady=8)
        source_box.columnconfigure(0,weight=1);source_box.rowconfigure(0,weight=1)
        self.source_tree=ttk.Treeview(source_box,columns=('name','type','path','snapshot'),show='headings',height=7,selectmode='browse')
        for name,label,width in [('name','Collection',120),('type','Type',80),('path','Source path',480),('snapshot','Closed snapshot',120)]:
            self.source_tree.heading(name,text=label);self.source_tree.column(name,width=width)
        self.source_tree.grid(row=0,column=0,sticky='nsew')
        down=ttk.Scrollbar(source_box,orient='vertical',command=self.source_tree.yview);down.grid(row=0,column=1,sticky='ns')
        across=ttk.Scrollbar(source_box,orient='horizontal',command=self.source_tree.xview);across.grid(row=1,column=0,sticky='ew')
        self.source_tree.configure(yscrollcommand=down.set,xscrollcommand=across.set)
        self.sources=[dict(s) for s in self.raw.get('sources',[])];self.draw_sources()
        row=ttk.Frame(library_page);row.pack(fill='x')
        self.button(row,'Add PDF folder…',lambda:self.add_source('folder'),side='left')
        self.button(row,'Add webclipper DB…',lambda:self.add_source('clipper'),side='left',padx=5)
        self.button(row,'Stop updating selected',self.remove_source,side='left')
        self.button(row,'Toggle closed snapshot',self.toggle_snapshot,side='left',padx=5)
        ttk.Label(library_page,text='Closed snapshot is only for a stopped clipper. Live WAL/journal files are refused.',wraplength=760).pack(anchor='w',pady=4)
        model=ttk.LabelFrame(model_page,text='Local GGUF model',padding=8);model.pack(fill='x',pady=8);model.columnconfigure(1,weight=1)
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
        ttk.Label(model,text='F16 uses more cache memory; Q8/Q4 use less. Quantized V requires Flash Attention. Leave GPU headroom for your display; use fewer GPU layers or a smaller context.',wraplength=720).grid(row=6,column=0,columnspan=3,sticky='w',pady=5)
        rank=ttk.LabelFrame(rank_page,text='Optional reranker',padding=8);rank.pack(fill='x',pady=8);rank.columnconfigure(1,weight=1)
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
        ttk.Label(rank,text='Ranks FTS passages against your question. Manual searches use it when a Rank results for question is entered. Newest-first bypasses scoring. CUDA shares VRAM with the GGUF and display.',wraplength=720).grid(row=9,column=0,columnspan=3,sticky='w',pady=5)
        actions=self.setup_actions
        self.button(actions,'Save settings',self.save,side='left')
        self.button(actions,'Refresh index',self.ingest,side='left',padx=5)
        self.button(actions,'Index status',self.index_status,side='left')
        ttk.Label(actions,text='Changes apply on the next operation.').pack(side='right',padx=8)
        model_actions=ttk.Frame(model_page);model_actions.pack(fill='x',pady=8)
        self.button(model_actions,'Load & test model',self.model_check,side='left')
        self.button(model_actions,'Unload model',lambda:self.submit(self.unload),side='left',padx=6)
        self.button(model_actions,'Model diagnostics',self.model_diagnostics,side='left',padx=6)
        self.button(rank_page,'Unload / reset reranker',lambda:self.submit(self.unload_reranker),side='left',padx=5)

    def draw_sources(self):
        self.source_tree.delete(*self.source_tree.get_children())
        for i,s in enumerate(self.sources):self.source_tree.insert('', 'end',iid=str(i),values=(s['name'],s['type'],s['path'],'export' if s.get('network_mode')=='export' else 'yes' if s.get('closed_snapshot') or s.get('network_mode')=='closed_snapshot' else 'no'))

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
            note_tokens=int(self.vars['note_tokens'].get())
            if not 64<=note_tokens<=32768:raise ValueError('Notebook budget must be 64..32768 tokens.')
            data['agent_options']={**self.raw.get('agent_options',{}),'note_tokens':note_tokens,'effort':self.effort.get()}
            data['search_input_mode']=self.search_input_mode.get()
            data['library_sync']={**data.get('library_sync',{}),'enabled':self.vars['managed_library'].get(),'auto_sync':self.vars['auto_sync'].get()}
            data['pdf_viewer']={'enabled':self.vars['pdf_page_enabled'].get(),'viewer':self.vars['pdf_viewer'].get(),'executable':self.vars['pdf_executable'].get().strip()}
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

    def resize_labels(self,event):
        if event.widget is self.root:
            width=max(320,event.width-48)
            self.status_label.configure(wraplength=width)
            self.metrics_label.configure(wraplength=width)

    def search_help(self):
        messagebox.showinfo('Search input',
            'Simple: words are ANDed; use "quoted phrases". ABC-123 is safe. Unmatched quotes report an error.\n\nAdvanced FTS5: use AND, OR, binary NOT, prefixes and NEAR.\nNEAR(export control, 5)\nNEAR("critical minerals" "supply chain", 10)\nNEAR("ABC-123" approval, 6)\n\nNEAR is unordered within an indexed passage. Hyphenated identifiers may also match spacing variants.\n\nEnter searches the library. Ctrl+Enter asks the model.',parent=self.root)

    def toggle_filters(self):
        if self.filters_visible.get():self.filter_panel.grid()
        else:self.filter_panel.grid_remove()

    def effort_changed(self,value=None):
        modes=('quick','balanced','thorough')
        self.effort.set('legacy' if self.legacy_policy.get() else modes[max(0,min(2,round(self.effort_level.get())))])

    def build_search(self):
        self.search_tab.columnconfigure(0,weight=1);self.search_tab.rowconfigure(2,weight=1)
        query=ttk.LabelFrame(self.search_tab,text='Search your library',padding=10)
        query.grid(row=0,column=0,sticky='ew',pady=(0,8));query.columnconfigure(0,weight=1)
        self.question=tk.StringVar()
        self.question_entry=ttk.Entry(query,textvariable=self.question,font=('Segoe UI',11))
        self.question_entry.grid(row=0,column=0,sticky='ew',ipady=4)
        self.question_entry.bind('<Return>',lambda event:self.search() if not self.busy else None)
        self.question_entry.bind('<Control-Return>',lambda event:self.question_model() if not self.busy else None)
        self.relevance_question=tk.StringVar()
        ttk.Label(query,text='Rank search results for question (optional)').grid(row=3,column=0,sticky='w',pady=(8,0))
        ttk.Entry(query,textvariable=self.relevance_question).grid(row=4,column=0,sticky='ew')
        steer_row=ttk.Frame(query);steer_row.grid(row=5,column=0,sticky='ew',pady=(6,0));steer_row.columnconfigure(0,weight=1)
        self.steering_text=tk.StringVar();self.steering_status=tk.StringVar(value='Steering applies on the next model turn.')
        ttk.Entry(steer_row,textvariable=self.steering_text).grid(row=0,column=0,sticky='ew')
        ttk.Button(steer_row,text='Queue instruction',command=self.queue_steering).grid(row=0,column=1,padx=6)
        ttk.Label(steer_row,textvariable=self.steering_status).grid(row=1,column=0,columnspan=2,sticky='w')
        row=ttk.Frame(query);row.grid(row=1,column=0,sticky='ew',pady=(8,0))
        self.button(row,'Search library',self.search,side='left')
        self.button(row,'Ask local model',self.question_model,side='left',padx=6)
        self.button(row,'Browse sources',self.browse,side='left')
        self.stop=ttk.Button(row,text='Stop operation',command=self.cancel.set,state='disabled');self.stop.pack(side='right')
        self.filters_visible=tk.BooleanVar(value=False)
        ttk.Checkbutton(row,text='Search options',variable=self.filters_visible,command=self.toggle_filters).pack(side='right',padx=10)
        effort_row=ttk.Frame(query);effort_row.grid(row=2,column=0,sticky='ew',pady=(8,0))
        configured=self.raw.get('agent_options',{}).get('effort','legacy');modes=('quick','balanced','thorough')
        self.effort=tk.StringVar(value=configured);self.effort_level=tk.DoubleVar(value=modes.index(configured) if configured in modes else 1)
        self.legacy_policy=tk.BooleanVar(value=configured=='legacy')
        ttk.Label(effort_row,text='Answer effort').pack(side='left')
        slider=ttk.Frame(effort_row);slider.pack(side='left',padx=10)
        scale=ttk.Scale(slider,from_=0,to=2,variable=self.effort_level,command=self.effort_changed,length=210)
        scale.grid(row=0,column=0,columnspan=3,sticky='ew')
        scale.bind('<ButtonRelease-1>',lambda event:scale.set(round(self.effort_level.get())))
        for i,label in enumerate(('Quick','Balanced','Thorough')):
            slider.columnconfigure(i,weight=1);ttk.Label(slider,text=label).grid(row=1,column=i)
        ttk.Label(effort_row,textvariable=self.effort,width=10).pack(side='left',padx=4)
        ttk.Checkbutton(effort_row,text='Legacy policy (comparison)',variable=self.legacy_policy,command=self.effort_changed).pack(side='left')

        self.filter_panel=ttk.LabelFrame(self.search_tab,text='Search options — library searches',padding=10)
        self.filter_panel.grid(row=1,column=0,sticky='ew',pady=(0,8));self.filter_panel.columnconfigure(5,weight=1)
        self.search_input_mode=tk.StringVar(value=self.raw.get('search_input_mode','simple'))
        ttk.Label(self.filter_panel,text='Input').grid(row=0,column=0,sticky='w')
        ttk.Combobox(self.filter_panel,textvariable=self.search_input_mode,values=('simple','advanced'),state='readonly',width=10).grid(row=0,column=1,sticky='w',padx=(6,16))
        ttk.Button(self.filter_panel,text='Search help',command=self.search_help).grid(row=0,column=2,sticky='w')
        self.exact_count=tk.BooleanVar(value=False)
        ttk.Checkbutton(self.filter_panel,text='Count all matches',variable=self.exact_count).grid(row=0,column=3,columnspan=3,sticky='w',padx=12)
        self.date_field=tk.StringVar(value='added_at');self.after=tk.StringVar();self.before=tk.StringVar();self.sort=tk.StringVar(value='relevance')
        ttk.Label(self.filter_panel,text='Date type').grid(row=1,column=0,sticky='w',pady=(8,0))
        ttk.Combobox(self.filter_panel,textvariable=self.date_field,values=('added_at','modified_at','published_at'),width=13,state='readonly').grid(row=1,column=1,sticky='w',padx=(6,16),pady=(8,0))
        for column,label,var in ((2,'After',self.after),(4,'Before',self.before)):
            ttk.Label(self.filter_panel,text=label).grid(row=1,column=column,sticky='w',pady=(8,0))
            ttk.Entry(self.filter_panel,textvariable=var,width=12).grid(row=1,column=column+1,sticky='w',padx=6,pady=(8,0))
        ttk.Label(self.filter_panel,text='Order').grid(row=2,column=0,sticky='w',pady=(8,0))
        ttk.Combobox(self.filter_panel,textvariable=self.sort,values=('relevance','newest'),width=13,state='readonly').grid(row=2,column=1,sticky='w',padx=(6,16),pady=(8,0))
        ttk.Label(self.filter_panel,text='YYYY-MM-DD · added/captured dates do not establish publication.',wraplength=470).grid(row=2,column=2,columnspan=4,sticky='w',pady=(8,0))
        self.filter_panel.grid_remove()

        self.workspace=ttk.Panedwindow(self.search_tab,orient='horizontal')
        self.workspace.grid(row=2,column=0,sticky='nsew')
        results=ttk.LabelFrame(self.workspace,text='Library results',padding=8)
        results.columnconfigure(0,weight=1);results.rowconfigure(1,weight=1)
        self.result_summary=tk.StringVar(value='Search or browse to see sources here.')
        summary_label=ttk.Label(results,textvariable=self.result_summary,wraplength=340)
        summary_label.grid(row=0,column=0,sticky='w',pady=(0,6))
        results.bind('<Configure>',lambda event:summary_label.configure(wraplength=max(150,event.width-20)))
        table=ttk.Frame(results);table.grid(row=1,column=0,sticky='nsew');table.columnconfigure(0,weight=1);table.rowconfigure(0,weight=1)
        self.tree=ttk.Treeview(table,columns=('title','copies','date','preview'),displaycolumns=('title','copies','date'),show='headings',height=8,selectmode='browse')
        for name,label,width in [('title','Source',210),('copies','Copies',50),('date','Date',110),('preview','Text',260)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width,minwidth=45,stretch=name=='title')
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview);scroll.grid(row=0,column=1,sticky='ns')
        across=ttk.Scrollbar(table,orient='horizontal',command=self.tree.xview);across.grid(row=1,column=0,sticky='ew')
        self.tree.configure(yscrollcommand=scroll.set,xscrollcommand=across.set)
        self.tree.bind('<<TreeviewSelect>>',self.preview_result);self.tree.bind('<Double-1>',self.expand)
        ttk.Label(results,text='Select to preview · double-click for context',wraplength=340).grid(row=2,column=0,sticky='w',pady=6)
        pages=ttk.Frame(results);pages.grid(row=3,column=0,sticky='ew')
        self.button(pages,'More results',self.more_results,side='left')
        self.button(pages,'Read source',self.read_document,side='left',padx=4)
        self.button(pages,'Next page',lambda:self.read_document(next_page=True),side='left')
        self.detail_tabs=ttk.Notebook(self.workspace)
        self.answer_tab=ttk.Frame(self.detail_tabs,padding=6);activity=ttk.Frame(self.detail_tabs,padding=6);timing=ttk.Frame(self.detail_tabs,padding=6)
        self.detail_tabs.add(self.answer_tab,text='Answer / source');self.detail_tabs.add(activity,text='Activity');self.detail_tabs.add(timing,text='Timing')
        self.output=ScrolledText(self.answer_tab,wrap='word',height=8,width=30,font=('Segoe UI',10),padx=10,pady=10,relief='flat')
        self.output.pack(fill='both',expand=True)
        activity_label=ttk.Label(activity,text='Live model output and evidence checklist · unvalidated',wraplength=500)
        activity_label.pack(anchor='w',pady=(0,6))
        activity.bind('<Configure>',lambda event:activity_label.configure(wraplength=max(150,event.width-16)))
        self.live=ScrolledText(activity,wrap='word',height=8,width=30,font=('Consolas',9),padx=8,pady=8,state='disabled',relief='flat');self.live.pack(fill='both',expand=True)
        self.thinking_tab=ttk.Frame(self.detail_tabs,padding=6);self.detail_tabs.add(self.thinking_tab,text='Thinking')
        self.thinking_live=ScrolledText(self.thinking_tab,wrap='word',height=8,width=30,font=('Consolas',9),padx=8,pady=8,state='disabled',relief='flat');self.thinking_live.pack(fill='both',expand=True)
        activity_label.configure(text='Tool actions, steering and evidence checklist · unvalidated')
        self.timing_details=ScrolledText(timing,wrap='word',height=8,width=30,font=('Consolas',9),padx=8,pady=8,state='disabled',relief='flat');self.timing_details.pack(fill='both',expand=True)
        self.workspace.add(results,weight=2);self.workspace.add(self.detail_tabs,weight=3)
        self.metrics=tk.StringVar();self.metrics_label=ttk.Label(self.search_tab,textvariable=self.metrics,wraplength=1000)
        self.metrics_label.grid(row=3,column=0,sticky='ew',pady=(6,3))
        self.ingest_bar=ttk.Progressbar(self.search_tab,mode='determinate',maximum=1,value=0)
        self.ingest_bar.grid(row=4,column=0,sticky='ew',pady=(2,0))
        self.show('Ask a question or search your library.\n\nAnswers, source passages and clickable citations appear here. Use Activity to follow progress and Timing for the detailed breakdown.')

    def preview_result(self,event=None):
        if self.busy:return
        selected=self.tree.selection()
        if not selected or selected[0] not in self.hits:return
        hit=self.hits[selected[0]]
        if 'passage_id' in hit:
            self.show(hit['text'])
            self.source_links([dict(location,id=location['source_id']) for location in hit['locations']])
        else:
            self.show(hit['title']+'\n'+hit['locator']+'\n\nStatus: '+hit['status']+'\n'+hit.get('detail',''))

    def show(self,text):
        self.hide_hint()
        self.detail_tabs.select(self.answer_tab)
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
            tag='source-'+str(source['id']);label=f"[{source['id']}] {source['title']}"+(f" · PDF page {source['page']}" if type(source.get('page')) is int else '')
            self.output.insert('end',label+'\n',tag);self.output.tag_configure(tag,foreground='#145da0',underline=True)
            detail='\n'.join([source['title'],source['locator']]+[f'{k}: {source[k]}' for k in ('added_at','modified_at','published_at') if source.get(k)])
            self.output.tag_bind(tag,'<Enter>',lambda e,t=detail:(self.output.configure(cursor='hand2'),self.hint(e,t)))
            self.output.tag_bind(tag,'<Leave>',lambda e:(self.output.configure(cursor='xterm'),self.hide_hint()))
            self.output.tag_bind(tag,'<Button-1>',lambda e,s=dict(source):self.open_link(s))

    def open_link(self,source):
        self.hide_hint()
        try:
            result=open_source(source,self.raw.get('pdf_viewer'))
            if result and result.get('fallback'):self.status.set('Opened normally: '+result['fallback'])
        except Exception as error:messagebox.showerror('Open source',str(error),parent=self.root)

    def get_library(self,config):
        from .sqlite_paths import network_path
        managed=config.get('library_sync',{}).get('enabled',False) or network_path(config['database'])
        management_key=(config['database'],config.get('library_sync',{}).get('local_root')) if managed else None
        if management_key!=self.management_key:
            if self.library:self.library.close();self.library=None;self.library_key=None
            if self.manager:self.manager.close();self.manager=None
            self.management_key=None;self.manual_searcher=None
        if managed and self.manager is None:
            from .library_management import ManagedLibrary
            self.manager=ManagedLibrary(config['database'],root=config.get('library_sync',{}).get('local_root'),source_paths=[s['path'] for s in config['sources']])
            self.management_key=management_key
        path=str(self.manager.path) if self.manager else config['database']
        key=(path,int(config.get('database_options',{}).get('cache_mb',32)))
        if key!=self.library_key:
            if self.library:self.library.close()
            self.library=None;self.library_key=None
            self.library=Library(key[0],cache_mb=key[1]);self.library_key=key
            self.manual_searcher=None
        return self.library

    def queue_steering(self):
        try:
            if self.active_steering is None:raise ValueError('Start a model question before queuing an instruction')
            item=self.active_steering.put(self.steering_text.get());self.steering_text.set('')
            self.steering_status.set('Queued instruction '+str(item['id'])+' for the next turn')
            self.emit({'type':'steering','status':'queued',**item})
        except ValueError as error:self.steering_status.set(str(error))

    def sync_status(self):
        if self.busy or not self.save():return
        config=self.cfg()
        def work():
            self.get_library(config)
            return self.manager.status() if self.manager else {'mode':'Direct local library; AppData sync disabled'}
        self.submit(work,self.display_report)

    def sync_now(self):
        if self.busy or not self.save():return
        config=self.cfg()
        def work():
            self.get_library(config)
            if not self.manager:raise ValueError('Enable AppData working copy or select a network library first')
            self.emit({'type':'phase','text':'Publishing consistent library snapshot…'})
            return self.manager.sync(self.cancel)
        self.submit(work,self.display_report,inference=True)

    def model_diagnostics(self):
        if self.busy:return
        def work():
            if not self.model:return {'status':'Load the model first to inspect runtime diagnostics'}
            from .diagnostics import memory_observations
            return dict(self.model.diagnostics(),memory=memory_observations())
        self.submit(work,self.display_report)

    def load_published(self):
        if self.busy or not self.save():return
        if not messagebox.askyesno('Load published copy','Replace the local working copy with the published copy? Any pending local work will be saved in before-network-import.db in the AppData library folder. This chooses the published version; it does not merge changes.',parent=self.root):return
        config=self.cfg()
        def work():
            self.get_library(config)
            if not self.manager:raise ValueError('Enable AppData working copy first')
            if self.library:self.library.close();self.library=None;self.library_key=None
            self.manual_searcher=None
            return self.manager.adopt_published(preserve_pending=True)
        def show(result):
            self.hits={};self.tree.delete(*self.tree.get_children());self.next_offset=None;self.open_doc=None;self.next_char=None
            self.display_report(result)
        self.submit(work,show)

    def emit(self,event):self.events.put(event)

    def ingest_progress(self,event):
        # Coalesce fast file updates instead of filling the inference event queue.
        with self.ingest_lock:self.ingest_latest=(event,time.monotonic())

    def display_ingest_progress(self):
        with self.ingest_lock:latest=self.ingest_latest
        if not latest:return
        original,received=latest;event=dict(original)
        event['elapsed_seconds']+=time.monotonic()-received
        event['last_event_age_seconds']=time.monotonic()-received
        prefix=(f"Source {event['source_number']}/{event.get('source_count',1)} ({event.get('source_collection',event['collection'])}) · "
                if event.get('source_number',0)>0 else '' if event['collection']=='library' else f"Source 1/{event.get('source_count',1)} · ")
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
        self.stop.configure(state='normal' if inference or self.ingesting else 'disabled')
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
                if self.manager:self.manager.close();self.manager=None
            self.results.put((result,error,callback))

    def poll(self):
        if self.ingesting:self.display_ingest_progress()
        # A fixed-size queue plus batched UI writes bounds streaming overhead and memory.
        text=[];thought=[]
        def split(parts):
            for channel,chunk in parts:(thought if channel=='thinking' else text).append(chunk)
        for _ in range(256):
            try:event=self.events.get_nowait()
            except queue.Empty:break
            if event['type']=='text':split(self.display_stream.feed(event['text']))
            elif event['type']=='stream_end':split(self.display_stream.feed('',final=True))
            elif event['type']=='round':
                split(self.display_stream.feed('',final=True))
                from .display_stream import DisplayStream
                self.display_stream=DisplayStream(event.get('thinking',False))
                text.append(f"\n--- Model round {event['round']} ---\n");thought.append(f"\n--- Model round {event['round']} ---\n")
            elif event['type']=='phase':self.status.set(event['text'])
            elif event['type']=='steering':
                text.append('\nInstruction '+str(event['id'])+' '+event['status']+': '+event['text']+'\n')
                self.steering_status.set('Instruction '+str(event['id'])+' '+event['status']+(f" on round {event['applied_round']}" if event.get('applied_round') else ''))
            elif event['type']=='notebook':
                text.append(f"\nNotebook: {event['used_tokens']}/{event['limit_tokens']} tokens, {event['remaining_tokens']} free, {event['items']} items.\n")
                if event.get('error'):text.append('Save rejected: '+event['error']+'\n')
            elif event['type']=='coverage':
                text.append('\nEvidence checklist:\n'+'\n'.join(item['status']+': '+item['need'] for item in event['items'])+'\n')
        if text:
            self.live.configure(state='normal');self.live.insert('end',''.join(text))
            if int(self.live.index('end-1c').split('.')[0])>1000:self.live.delete('1.0','end-1000lines')
            if len(self.live.get('1.0','end-1c'))>32000:self.live.delete('1.0','end-32000c')
            self.live.see('end');self.live.configure(state='disabled')
        if thought:
            self.thinking_live.configure(state='normal');self.thinking_live.insert('end',''.join(thought))
            if len(self.thinking_live.get('1.0','end-1c'))>32000:self.thinking_live.delete('1.0','end-32000c')
            self.thinking_live.see('end');self.thinking_live.configure(state='disabled')
        try:
            result,error,callback=self.results.get_nowait();self.busy=False
            self.active_steering=None
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
        if not self.busy and self.manager and self.raw.get('library_sync',{}).get('auto_sync',False) and time.monotonic()>=self.next_sync_check:
            self.next_sync_check=time.monotonic()+15
            def background_sync():
                if self.manager.due():
                    try:return self.manager.sync(self.cancel)
                    except Exception:return self.manager.status()
                return None
            def sync_finished(result):
                if result:self.status.set('Library sync pending: '+result['error'] if result.get('error') else 'Library snapshot synced to '+result['network_path'])
            self.submit(background_sync,sync_finished,inference=True)
        self.root.after(80,self.poll)

    def ingest(self,automatic=False):
        if self.busy:return
        if not automatic and not self.save():return
        if not automatic:self.tabs.select(self.search_tab)
        config=self.cfg()
        def work():
            self.ingest_progress({'type':'ingest_progress','collection':'library','phase':'Opening database','current':config['database'],
                                  'processed':0,'total':None,'stats':{},'elapsed_seconds':0,'source_count':len(config['sources'])})
            return refresh(self.get_library(config),config,on_progress=self.ingest_progress,cancel_event=self.cancel)
        self.next_refresh=time.monotonic()+max(10,int(self.raw.get('refresh_seconds',0)))
        self.ingesting=True
        with self.ingest_lock:self.ingest_latest=None
        self.ingest_bar.configure(mode='indeterminate');self.ingest_bar.start(80)
        def show(reports):
            count=sum(r.get('processed',r.get('indexed',0)+r.get('skipped',0)+r.get('errors',0)) for r in reports)
            errors=sum(r.get('errors',0)+len(r.get('scan_errors',[]))+('error' in r) for r in reports)
            skipped=sum(r.get('skipped',0) for r in reports);cached=sum(r.get('cached',0) for r in reports)
            cancelled=any(r.get('cancelled') for r in reports)
            if not cancelled:self.ingest_bar.configure(mode='determinate',maximum=1,value=1)
            log_path=reports[0].get('log_file') if reports else None
            self.status.set(('Refresh cancelled' if cancelled else 'Refresh complete with errors' if errors else 'Refresh complete')+f': {count} items processed, {cached} cached, {skipped} skipped, {errors} errors. Log: '+str(log_path or Path(config['database']).parent/'ingestion.log'))
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
        config=self.cfg();query=self.question.get();options=dict(after=self.after.get() or None,before=self.before.get() or None,date_field=self.date_field.get(),sort=self.sort.get(),limit=20,offset=offset,exact_count=self.exact_count.get(),input_mode=self.search_input_mode.get(),_relevance_question=self.relevance_question.get().strip())
        if offset and getattr(self,'search_request',None):
            query,options=self.search_request;options=dict(options,offset=offset)
        else:self.search_request=(query,dict(options))
        submitted=time.perf_counter()
        def work():
            started=time.perf_counter();lib=self.get_library(config);opened=time.perf_counter()
            arguments=dict(options);relevance=arguments.pop('_relevance_question')
            if relevance:
                from .rerank import QuestionSearch
                if not offset or self.manual_searcher is None:
                    ranker=self.loaded_reranker(config)
                    if ranker is None:raise ValueError('Enable a reranker in Settings to rank results for a question, or clear the relevance question for ordinary FTS5')
                    self.manual_searcher=QuestionSearch(lib,ranker,relevance,self.cancel,self.emit)
                before_fts=self.manual_searcher.fts_seconds;before_rank=self.manual_searcher.rerank_seconds
                result=self.manual_searcher.search(query=query,relevance_query=relevance,**arguments)
                result['timings']={'fts_seconds':self.manual_searcher.fts_seconds-before_fts,'rerank_seconds':self.manual_searcher.rerank_seconds-before_rank}
                result['reranking_trace']=self.manual_searcher.trace
            else:
                self.manual_searcher=None;result=lib.search(query,**arguments)
            result['timings'].update(queue_seconds=started-submitted,connection_seconds=opened-started)
            return result
        def show(result):
            render_started=time.perf_counter()
            self.next_offset=result['next_offset'];self.result_mode='search'
            self.result_summary.set(str(len(result['results']))+' passages on this page'+(' · bounded reranked shortlist' if result.get('match_scope')=='candidate_pool' else '')+(' · more available' if self.next_offset is not None else ''))
            self.tree.delete(*self.tree.get_children());self.hits={}
            for i,hit in enumerate(result['results']):
                self.hits[str(i)]=hit;loc=hit['locations'][0]
                self.tree.insert('','end',iid=str(i),values=(loc['title'],hit['location_count'],hit['newest'] or 'unknown',hit['text'][:200]))
            total=result['total_unique_passages'];self.show((f'{total} unique passages.' if total is not None else 'Exact total not requested.')+' Showing up to 20.\n'+result['date_note']+'\nCompiled FTS5: '+result['query'])
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
            self.result_summary.set(str(len(rows))+' sources on this page'+(' · more available' if self.next_offset is not None else ''))
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
            from .inference_lock import InferenceLease
            self.model_lease=InferenceLease()
            try:
                started=time.perf_counter();self.model=GGUF(**config['model']);self.model_key=key
            except BaseException:self.model_lease.close();self.model_lease=None;raise
            self.last_load_seconds=time.perf_counter()-started
        else:self.last_load_seconds=0
        self.model.cancel_event=self.cancel;return self.model

    def unload(self):
        if self.model:self.model.close();self.model=None;self.model_key=None
        if self.model_lease:self.model_lease.close();self.model_lease=None
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
            return {'response':result['choices'][0]['text'],'usage':result.get('usage'),'timings':result.get('timings'),'load_seconds':self.last_load_seconds,'DLL folders':model.dlls.paths,'diagnostics':model.diagnostics()}
        self.submit(work,self.display_report,inference=True)

    def question_model(self):
        clicked=time.perf_counter()
        if not self.save():return
        config=self.cfg();question=self.question.get().strip()
        if not question:return
        self.question_serial+=1;question_serial=self.question_serial
        from .steering import SteeringQueue
        steering=SteeringQueue();self.active_steering=steering;self.steering_status.set('Question running; instructions can be queued.')
        self.live.configure(state='normal');self.live.delete('1.0','end');self.live.configure(state='disabled')
        self.thinking_live.configure(state='normal');self.thinking_live.delete('1.0','end');self.thinking_live.configure(state='disabled')
        submitted=time.perf_counter();clock={}
        def work():
            from .agent import ask
            clock['worker_started']=time.perf_counter()
            started=time.perf_counter()
            model=self.loaded_model(config)
            clock['load_seconds']=time.perf_counter()-started
            started=time.perf_counter();library=self.get_library(config)
            clock['library_seconds']=time.perf_counter()-started
            result=ask(library,model,question,on_event=self.emit,prompts_file=config.get('prompts_file'),reranker=self.loaded_reranker(config),note_tokens=config.get('agent_options',{}).get('note_tokens',6000),effort=config.get('agent_options',{}).get('effort','legacy'),steering=steering)
            result['timings']['load_seconds']=clock['load_seconds']
            trace=library.path.parent/'last-gui-search.json';clock['trace']=trace
            with self.trace_lock:trace.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
            clock['worker_finished']=time.perf_counter()
            return result
        def show(result):
            self.active_steering=None;self.steering_status.set('Question complete; see Activity for steering history.')
            render_started=time.perf_counter()
            display=result.get('answer') or result['status']
            if result.get('gaps'):display+='\n\nUnresolved points:\n'+'\n'.join('• '+gap for gap in result['gaps'])
            self.show(display);self.source_links(result.get('sources',[]))
            self.output.update_idletasks()
            completed=time.perf_counter()
            timing=complete_timing(result,clicked,submitted,clock['worker_started'],clock['worker_finished'],render_started,completed,clock['load_seconds'],clock['library_seconds'])
            result['gui_timing']=timing
            self.timing_details.configure(state='normal');self.timing_details.delete('1.0','end');self.timing_details.insert('end',format_timing(timing));self.timing_details.configure(state='disabled')
            checks=result.get('context_checks',[])
            rates=[c['timings']['approx_decode_tokens_per_second'] for c in checks if c.get('timings',{}).get('approx_decode_tokens_per_second') is not None]
            label='Click → final answer complete' if result['status'] in ('answered','answered_with_gaps') else 'Click → stopped ('+result['status']+')'
            self.metrics.set(f"{label}: {timing['click_to_complete_seconds']:.2f}s | FTS searches {result['timings']['fts_search_seconds']:.2f}s | {timing['model_rounds']} model rounds"+(f' | Last round ~{rates[-1]:.1f} tok/s' if rates else '')+' | See timing breakdown')
            if 'reranking' in result:
                applied=sum(t['rerank']['applied'] for t in result['reranking']);fallbacks=[t['rerank']['reason'] for t in result['reranking'] if t['rerank']['reason']]
                self.metrics.set(self.metrics.get()+f" | Rerank {result['timings'].get('rerank_seconds',0):.2f}s ({applied} applied)"+(' | Fallback: '+', '.join(dict.fromkeys(fallbacks)) if fallbacks else ''))
            if 'notebook' in result:
                usage=result['notebook']
                self.metrics.set(self.metrics.get()+f" | Notebook {usage['used_tokens']}/{usage['limit_tokens']} tokens ({usage['items']} items)")
            self.metrics.set(self.metrics.get()+' | '+result.get('effort','legacy')+' · '+result.get('stop_reason',result['status']))
            # Save final GUI measurements after the answer is displayed. This background
            # persistence is deliberately outside click-to-answer completion time.
            trace=clock['trace']
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
