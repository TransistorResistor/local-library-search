"""Standalone native review window. No model is loaded by the review application."""
import argparse
import copy
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from tkinter.scrolledtext import ScrolledText
from .metadata import database_path
from .metadata_profile import canonical
from . import metadata_review as review


def summary(item):
    if item is None:return 'Unknown'
    if not isinstance(item,dict):return 'Invalid: '+json.dumps(item,ensure_ascii=False)
    value=item.get('value')
    if isinstance(value,list):return '; '.join(str(v.get('value',v.get('scope',''))) if isinstance(v,dict) else str(v) for v in value)
    return str(value)


class MetadataApp:
    def __init__(self,root,config_path):
        from .__main__ import configuration
        self.root=root;self.config_path=config_path;self.path=database_path(configuration(config_path))
        self.record=None;self.values={};self.manual=set();self.field=None;self.busy=False;self.dirty=False;self.results=queue.Queue();self.list_items=[];self.offset=0;self.page_total=0;self.locked_widgets=[];self.raw_mode=tk.BooleanVar(value=False)
        root.title('Metadata review');root.geometry('1180x800');root.minsize(900,650)
        self.status=tk.StringVar(value='Loading findings…');self.filter=tk.StringVar(value='needs_review')
        bar=ttk.Frame(root,padding=12);bar.pack(fill='x')
        ttk.Label(bar,text='Metadata review',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Combobox(bar,textvariable=self.filter,values=('needs_review','accepted','extracted','unknown','failed','all'),state='readonly',width=16).pack(side='right')
        ttk.Button(bar,text='Refresh',command=self.refresh).pack(side='right',padx=8)
        filters=ttk.Frame(root,padding=(12,0));filters.pack(fill='x')
        self.query=tk.StringVar();self.collection=tk.StringVar();self.profile_filter=tk.StringVar();self.queue_status=tk.StringVar()
        for label,var in [('Title',self.query),('Collection',self.collection),('Profile',self.profile_filter)]:
            ttk.Label(filters,text=label).pack(side='left',padx=(0,4))
            entry=ttk.Entry(filters,textvariable=var,width=18) if var is self.query else ttk.Combobox(filters,textvariable=var,width=18)
            entry.pack(side='left',padx=(0,10))
            if var is self.collection:self.collection_box=entry
            elif var is self.profile_filter:self.profile_box=entry
        ttk.Button(filters,text='Filter',command=lambda:self.refresh(0)).pack(side='left')
        ttk.Label(root,textvariable=self.queue_status,padding=(12,4)).pack(fill='x')
        pagebar=ttk.Frame(root,padding=(12,4));pagebar.pack(fill='x')
        ttk.Button(pagebar,text='Previous',command=lambda:self.refresh(max(0,self.offset-50))).pack(side='left')
        ttk.Button(pagebar,text='Next',command=lambda:self.refresh(self.offset+50) if self.offset+50<self.page_total else None).pack(side='left',padx=6)
        ttk.Button(pagebar,text='Extraction controls…',command=self.open_workflow).pack(side='right')
        panes=ttk.Panedwindow(root,orient='horizontal');panes.pack(fill='both',expand=True,padx=12)
        left=ttk.Frame(panes);right_host=ttk.Frame(panes);panes.add(left,weight=1);panes.add(right_host,weight=3)
        self.right_canvas=tk.Canvas(right_host,highlightthickness=0);self.right_canvas.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(right_host,orient='vertical',command=self.right_canvas.yview);scroll.pack(side='right',fill='y');self.right_canvas.configure(yscrollcommand=scroll.set)
        right=ttk.Frame(self.right_canvas,padding=(12,0));right_window=self.right_canvas.create_window((0,0),window=right,anchor='nw')
        right.bind('<Configure>',lambda event:self.right_canvas.configure(scrollregion=self.right_canvas.bbox('all')))
        self.right_canvas.bind('<Configure>',lambda event:self.right_canvas.itemconfigure(right_window,width=event.width))
        self.jobs=ttk.Treeview(left,columns=('title','status'),show='headings',selectmode='browse')
        self.jobs.heading('title',text='Report');self.jobs.heading('status',text='Status');self.jobs.column('title',width=240);self.jobs.column('status',width=100)
        self.jobs.pack(side='left',fill='both',expand=True);scroll=ttk.Scrollbar(left,orient='vertical',command=self.jobs.yview);scroll.pack(side='right',fill='y');self.jobs.configure(yscrollcommand=scroll.set);self.jobs.bind('<<TreeviewSelect>>',self.select_job)
        self.title=tk.StringVar(value='Choose a finding');ttk.Label(right,textvariable=self.title,font=('Segoe UI',12,'bold'),wraplength=720).pack(anchor='w',pady=(0,8))
        self.fields=ttk.Treeview(right,columns=('field','value','state'),show='headings',height=6,selectmode='browse')
        for name,label,width in [('field','Field',150),('value','Finding',410),('state','Review',110)]:self.fields.heading(name,text=label);self.fields.column(name,width=width)
        self.fields.pack(fill='x');self.fields.bind('<<TreeviewSelect>>',self.select_field)
        self.comparison=tk.StringVar();ttk.Label(right,textvariable=self.comparison,wraplength=720).pack(anchor='w',pady=4)
        self.field_issue=tk.StringVar();ttk.Label(right,textvariable=self.field_issue,wraplength=720,foreground='#9b381e').pack(anchor='w')
        self.editor=ttk.LabelFrame(right,text='Edit finding',padding=8);self.editor.pack(fill='x',pady=8)
        self.unknown=tk.BooleanVar();ttk.Checkbutton(self.editor,text='Unknown / no value',variable=self.unknown).pack(anchor='w')
        ttk.Checkbutton(self.editor,text='Edit raw field JSON',variable=self.raw_mode,command=self.reload_field).pack(anchor='w')
        self.value_text=ScrolledText(self.editor,height=3,wrap='word',font=('Segoe UI',10));self.value_text.pack(fill='x',pady=4)
        self.value_text.bind('<KeyRelease>',lambda event:self.unknown.set(False) if self.value_text.get('1.0','end-1c').strip() else None)
        self.precision=tk.StringVar(value='day');self.scope=tk.StringVar()
        detail=ttk.Frame(self.editor);detail.pack(fill='x');self.scalar_detail=detail
        ttk.Label(detail,text='Date precision').pack(side='left');self.precision_box=ttk.Combobox(detail,textvariable=self.precision,values=('year','month','day'),state='readonly',width=8);self.precision_box.pack(side='left',padx=6)
        ttk.Label(detail,text='Scope').pack(side='left');ttk.Entry(detail,textvariable=self.scope).pack(side='left',fill='x',expand=True,padx=6)
        self.list_frame=ttk.Frame(self.editor)
        self.list_tree=ttk.Treeview(self.list_frame,columns=('start','precision','end','end_precision','scope'),show='headings',height=3)
        for name in ('start','precision','end','end_precision','scope'):self.list_tree.heading(name,text=name.replace('_',' ').title());self.list_tree.column(name,width=100)
        self.list_tree.pack(fill='x');self.list_tree.bind('<<TreeviewSelect>>',self.select_list_item)
        row=ttk.Frame(self.list_frame);row.pack(fill='x',pady=4);self.start=tk.StringVar();self.end=tk.StringVar();self.end_precision=tk.StringVar(value='day');self.item_scope=tk.StringVar();self.item_precision=tk.StringVar(value='day')
        for index,(label,var,width) in enumerate([('Date / start',self.start,12),('Precision',self.item_precision,7),('End',self.end,12),('Precision',self.end_precision,7),('Scope',self.item_scope,22)]):
            r=index//2;c=(index%2)*2;ttk.Label(row,text=label).grid(row=r,column=c,sticky='w',padx=3,pady=2)
            widget=ttk.Combobox(row,textvariable=var,values=('year','month','day'),state='readonly',width=width) if label=='Precision' else ttk.Entry(row,textvariable=var,width=width)
            widget.grid(row=r,column=c+1,sticky='ew',padx=3)
        row=ttk.Frame(self.list_frame);row.pack(fill='x')
        for label,command in [('Add date / period',self.add_list_item),('Update selected',lambda:self.add_list_item(True)),('Remove selected',self.remove_list_item)]:ttk.Button(row,text=label,command=command).pack(side='left',padx=3)
        self.list_values=[];self.help=tk.StringVar();self.help_label=ttk.Label(self.editor,textvariable=self.help,wraplength=700);self.help_label.pack(anchor='w',pady=4)
        ttk.Button(self.editor,text='Apply field edit',command=self.apply_field).pack(anchor='w')
        ttk.Button(self.editor,text='Reset field to unknown',command=self.reset_field).pack(anchor='w')
        self.details=ttk.Notebook(right);self.details.pack(fill='both',expand=True)
        evidence_page=ttk.Frame(self.details);attempt_page=ttk.Frame(self.details);self.details.add(evidence_page,text='Evidence');self.details.add(attempt_page,text='Attempts / prompt')
        self.evidence=ScrolledText(evidence_page,wrap='word',height=8,state='disabled');self.evidence.pack(fill='both',expand=True)
        self.attempts=ScrolledText(attempt_page,wrap='word',height=8,state='disabled');self.attempts.pack(fill='both',expand=True)
        self.quote=tk.StringVar();self.excerpt=tk.StringVar()
        evidence_bar=ttk.Frame(evidence_page);evidence_bar.pack(fill='x',pady=4)
        self.excerpt_box=ttk.Combobox(evidence_bar,textvariable=self.excerpt,state='readonly',width=5);self.excerpt_box.pack(side='left')
        ttk.Entry(evidence_bar,textvariable=self.quote).pack(side='left',fill='x',expand=True,padx=4)
        ttk.Button(evidence_bar,text='Use quote',command=self.set_quote).pack(side='left');ttk.Button(evidence_bar,text='Clear quotes',command=self.clear_quotes).pack(side='left',padx=4)
        actions=ttk.Frame(root,padding=12);actions.pack(side='bottom',fill='x',before=panes)
        self.save_button=ttk.Button(actions,text='Save draft',command=lambda:self.save(False));self.save_button.pack(side='left')
        self.accept_button=ttk.Button(actions,text='Accept finding',command=lambda:self.save(True));self.accept_button.pack(side='left',padx=8)
        ttk.Button(actions,text='Accept and next',command=lambda:self.save(True,advance=True)).pack(side='left',padx=8)
        ttk.Button(actions,text='Open original',command=self.open_original).pack(side='left',padx=8)
        ttk.Label(actions,text='Edited fields become protected manual corrections when accepted.',wraplength=600).pack(side='left',padx=12)
        status_label=ttk.Label(root,textvariable=self.status,padding=(12,4),wraplength=1100);status_label.pack(side='bottom',fill='x',before=panes)
        root.bind('<Control-s>',lambda e:self.save(False));root.bind('<Control-Return>',lambda e:self.save(True,advance=True))
        for tree in (self.jobs,self.fields,self.list_tree):
            tree.bind('<Button-1>',lambda event:'break' if self.busy else None,add='+');tree.bind('<Key>',lambda event:'break' if self.busy else None,add='+')
        self.filter.trace_add('write',lambda *args:self.refresh());root.after(80,self.poll);root.protocol('WM_DELETE_WINDOW',self.close);self.refresh()

    def submit(self,work,done):
        if self.busy:return
        self.busy=True;self.lock_inputs(True)
        def run():
            try:self.results.put((work(),None,done))
            except Exception as error:self.results.put((None,str(error),done))
        threading.Thread(target=run,daemon=True).start()

    def lock_inputs(self,locked):
        if not locked:
            for widget,state,kind in self.locked_widgets:
                if widget.winfo_exists():
                    if kind=='ttk':widget.state(['!disabled'] if 'disabled' not in state else ['disabled'])
                    else:widget.configure(state=state)
            self.locked_widgets=[];return
        def visit(widget):
            for child in widget.winfo_children():
                try:
                    if isinstance(child,ttk.Widget) and 'state' in child.keys():
                        state=child.state();child.state(['disabled']);self.locked_widgets.append((child,state,'ttk'))
                    elif child.winfo_class() in ('Text','Entry'):
                        state=child.cget('state');child.configure(state='disabled');self.locked_widgets.append((child,state,'tk'))
                    elif isinstance(child,ttk.Treeview):
                        state=child.state();child.state(['disabled']);self.locked_widgets.append((child,state,'ttk'))
                except tk.TclError:pass
                visit(child)
        visit(self.root)

    def poll(self):
        try:
            result,error,done=self.results.get_nowait();self.busy=False;self.lock_inputs(False)
            if error:raise ValueError(error)
            done(result)
        except queue.Empty:pass
        except Exception as error:
            self.busy=False;self.lock_inputs(False);self.status.set(str(error));messagebox.showerror('Metadata review',str(error),parent=self.root)
        finally:
            if self.root.winfo_exists():self.root.after(80,self.poll)

    def refresh(self,offset=0,select_first=False,select_index=None):
        if self.busy:return
        if self.record and not self.apply_field():return
        if self.dirty and not messagebox.askyesno('Unsaved edits','Discard unsaved edits and refresh?',parent=self.root):return
        status=self.filter.get();query=self.query.get().strip();collection=self.collection.get().strip();profile=self.profile_filter.get().strip()
        def done(page):
            self.record=None;self.field=None;self.values={};self.fields.delete(*self.fields.get_children());self.title.set('Choose a finding')
            self.offset=page['offset'];self.page_total=page['total'];self.list_items=page['items'];self.jobs.delete(*self.jobs.get_children())
            for item in page['items']:self.jobs.insert('','end',iid=item['id'],values=(item['title'] or 'Removed source',item['status']))
            self.collection_box.configure(values=['']+page['collections']);self.profile_box.configure(values=['']+page['profiles'])
            counts=page['counts'];self.queue_status.set(f"{page['total']} matching · showing {self.offset+1 if page['items'] else 0}–{self.offset+len(page['items'])} · "+' · '.join(f'{key}: {value}' for key,value in sorted(counts.items())))
            self.status.set(str(self.path));self.dirty=False;self.comparison.set('');self.field_issue.set('');self.value_text.delete('1.0','end');self._text(self.evidence,'');self._text(self.attempts,'');self.list_tree.delete(*self.list_tree.get_children())
            if select_first and not page['items'] and self.offset:
                self.refresh(max(0,self.offset-50),select_first=True);return
            if select_index is not None and page['items']:
                index=min(select_index,len(page['items'])-1);self.jobs.selection_set(page['items'][index]['id']);self.select_job()
            elif select_first and page['items']:self.jobs.selection_set(page['items'][0]['id']);self.select_job()
        self.submit(lambda:review.queue_page(self.path,status,query,collection,profile,offset=offset),done)

    def select_job(self,event=None):
        selection=self.jobs.selection()
        if self.busy or not selection:return
        if self.record and selection[0]==self.record['job']['id']:return
        if self.record and not self.apply_field():return
        if self.dirty and not messagebox.askyesno('Unsaved edits','Discard unsaved edits and open another finding?',parent=self.root):
            if self.record:self.jobs.selection_set(self.record['job']['id'])
            return
        self.submit(lambda:review.fetch(self.path,selection[0]),self.load_record)

    def load_record(self,record):
        self.record=record;self.values=copy.deepcopy(record['metadata'] or {f['name']:None for f in record['profile']['fields']});self.manual=set(record['manual_fields']);self.dirty=False;self.field=None
        self.jobs.selection_set(record['job']['id']) if self.jobs.exists(record['job']['id']) else None
        self.title.set(record['title']+(' · Source changed: acceptance blocked' if record['stale'] else ''))
        self.fields.delete(*self.fields.get_children())
        for field in record['profile']['fields']:
            name=field['name'];self.fields.insert('','end',iid=name,values=(name,summary(self.values.get(name)),'Manual edit' if name in self.manual else 'Review'))
        self._text(self.attempts,'Prompt:\n'+record['prompt']+'\n\nValidation issues:\n'+json.dumps(record['issues'],indent=2)+'\n\nAttempts:\n'+json.dumps(json.loads(record['job']['attempts']),ensure_ascii=False,indent=2))
        names=self.fields.get_children()
        if names:self.fields.selection_set(names[0]);self.select_field()
        self.status.set('Review the values and evidence, then save a draft or accept.');self.accept_button.configure(state='disabled' if record['stale'] else 'normal')

    @staticmethod
    def _text(widget,value):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('end',value);widget.configure(state='disabled')

    def select_field(self,event=None):
        selected=self.fields.selection()
        if self.busy or not self.record or not selected:return
        if self.field and self.field['name']==selected[0]:return
        if self.field and self.field['name']!=selected[0] and not self.apply_field():
            self.fields.selection_set(self.field['name']);return
        self.field=next(f for f in self.record['profile']['fields'] if f['name']==selected[0]);item=self.values.get(self.field['name']);self.unknown.set(item is None)
        kind=self.field['type'];self.raw_mode.set(self.malformed(item,kind));self.render_field(item)

    @staticmethod
    def malformed(item,kind):
        if item is None:return False
        if not isinstance(item,dict) or not {'value','evidence'}<=set(item) or not isinstance(item['evidence'],list):return True
        if kind=='string_list' and (not isinstance(item['value'],list) or any(not isinstance(value,str) for value in item['value'])):return True
        if kind in ('date_list','interval_list'):
            if not isinstance(item['value'],list):return True
            for member in item['value']:
                if not isinstance(member,dict):return True
                if kind=='interval_list' and any(member.get(k) is not None and not isinstance(member[k],dict) for k in ('start','end')):return True
        return False

    def reload_field(self):
        if self.field:self.render_field(self.values.get(self.field['name']))

    def render_field(self,item):
        kind=self.field['type'];value=item.get('value') if isinstance(item,dict) else '';self.unknown.set(item is None);self.value_text.delete('1.0','end')
        self.comparison.set('Current: '+summary(self.record.get('current_values',{}).get(self.field['name'],{}).get('value'))+' · Manual override: '+summary(self.record['overrides'].get(self.field['name'])))
        self.field_issue.set('; '.join(issue for issue in self.record['issues'] if issue.startswith(self.field['name']+':')))
        if self.raw_mode.get():
            self.value_text.insert('end',json.dumps(item,ensure_ascii=False,indent=2));self.list_frame.pack_forget();self.value_text.pack(fill='x',pady=4,before=self.help_label);self.scalar_detail.pack_forget()
            self.help.set('Invalid structure: correct the field JSON, or reset to unknown and enter a fresh value. The original extraction remains in the audit history.');self.show_evidence();return
        self.value_text.insert('end','\n'.join(value) if kind=='string_list' and isinstance(value,list) else str(value).lower() if kind=='boolean' else '' if kind in ('date_list','interval_list') else str(value))
        self.precision.set(item.get('precision','day') if isinstance(item,dict) else 'day');self.scope.set(item.get('scope','') if isinstance(item,dict) else '')
        self.list_values=copy.deepcopy(value) if kind in ('date_list','interval_list') and isinstance(value,list) else [];self.draw_list()
        if kind=='date' or self.field.get('scoped'):self.scalar_detail.pack(fill='x',before=self.help_label)
        else:self.scalar_detail.pack_forget()
        self.comparison.set('Current: '+summary(self.record.get('current_values',{}).get(self.field['name'],{}).get('value'))+' · Manual override: '+summary(self.record['overrides'].get(self.field['name'])))
        self.field_issue.set('; '.join(issue for issue in self.record['issues'] if issue.startswith(self.field['name']+':')))
        if kind in ('date_list','interval_list'):self.list_frame.pack(fill='x',pady=4);self.value_text.pack_forget()
        else:self.list_frame.pack_forget();self.value_text.pack(fill='x',pady=4,before=self.help_label)
        self.help.set(self.field.get('prompt','')+(' Use one item per line.' if kind=='string_list' else '')+(' A saved manual override already exists for this field.' if self.field['name'] in self.record['overrides'] else ''))
        self.show_evidence()

    def draw_list(self):
        self.list_tree.delete(*self.list_tree.get_children())
        for i,item in enumerate(self.list_values):
            start=item if self.field and self.field['type']=='date_list' else item.get('start') or {};end=item.get('end') or {}
            self.list_tree.insert('','end',iid=str(i),values=(start.get('value',''),start.get('precision',''),end.get('value',''),end.get('precision',''),item.get('scope','')))

    def select_list_item(self,event=None):
        selected=self.list_tree.selection()
        if not selected:return
        item=self.list_values[int(selected[0])];start=item if self.field['type']=='date_list' else item.get('start') or {};end=item.get('end') or {}
        self.start.set(start.get('value',''));self.item_precision.set(start.get('precision','day'));self.end.set(end.get('value',''));self.end_precision.set(end.get('precision','day'));self.item_scope.set(item.get('scope',''))

    def add_list_item(self,update=False):
        if not self.field:return
        start={'value':self.start.get().strip(),'precision':self.item_precision.get()};end={'value':self.end.get().strip(),'precision':self.end_precision.get()}
        item={**start,'scope':self.item_scope.get().strip()} if self.field['type']=='date_list' else {'start':start if start['value'] else None,'end':end if end['value'] else None,'scope':self.item_scope.get().strip()}
        selected=self.list_tree.selection()
        if update and selected:self.list_values[int(selected[0])]=item
        elif not update:self.list_values.append(item)
        self.unknown.set(False);self.draw_list();self.apply_field()

    def remove_list_item(self):
        selected=self.list_tree.selection()
        if selected:self.list_values.pop(int(selected[0]));self.draw_list();self.apply_field()

    def apply_field(self):
        if self.busy:return False
        if not self.field or not self.record:return True
        name=self.field['name'];kind=self.field['type'];old=self.values.get(name)
        try:
            if self.unknown.get():item=None
            elif self.raw_mode.get():item=json.loads(self.value_text.get('1.0','end-1c'))
            else:
                text=self.value_text.get('1.0','end-1c').strip()
                value=copy.deepcopy(self.list_values) if kind in ('date_list','interval_list') else [line.strip() for line in text.splitlines() if line.strip()] if kind=='string_list' else int(text) if kind=='integer' else float(text) if kind=='number' else {'true':True,'false':False}[text.lower()] if kind=='boolean' else text
                item={'value':value,'evidence':copy.deepcopy(old.get('evidence',[])) if isinstance(old,dict) else []}
                if kind=='date':item['precision']=self.precision.get()
                if self.field.get('scoped'):item['scope']=self.scope.get().strip()
            if canonical(item)!=canonical(old):
                self.values[name]=item;self.manual.add(name);self.dirty=True
                self.fields.item(name,values=(name,summary(item),'Manual edit'))
            return True
        except (ValueError,KeyError) as error:self.status.set('Check the value for '+name+': '+str(error));return False

    def show_evidence(self):
        if not self.field:return
        item=self.values.get(self.field['name']);quotes=item.get('evidence',[]) if isinstance(item,dict) else []
        text='Supporting quotes for this field:\n'+json.dumps(quotes,ensure_ascii=False,indent=2)+'\n\nOriginal excerpts:\n'
        for excerpt in self.record['excerpts']:text+='\n'+excerpt['excerpt_id']+' · pages '+str(excerpt.get('pages',[]))+'\n'+excerpt['text']+'\n'
        self._text(self.evidence,text);ids=[e['excerpt_id'] for e in self.record['excerpts']];self.excerpt_box.configure(values=ids)
        if ids:self.excerpt.set(ids[0])

    def set_quote(self):
        if not self.apply_field() or not self.field:return
        item=self.values.get(self.field['name'])
        if item is None:self.status.set('Set a value before adding evidence.');return
        if not isinstance(item,dict):self.status.set('Correct the field structure or reset it before adding quotes.');return
        text=next((e['text'] for e in self.record['excerpts'] if e['excerpt_id']==self.excerpt.get()),'')
        if not self.quote.get().strip() or self.quote.get() not in text:self.status.set('The quote must exactly match the selected excerpt.');return
        item['evidence']=[{'excerpt_id':self.excerpt.get(),'quote':self.quote.get()}];self.manual.add(self.field['name']);self.dirty=True;self.show_evidence()

    def clear_quotes(self):
        if not self.apply_field() or not self.field:return
        item=self.values.get(self.field['name'])
        if item is not None and not isinstance(item,dict):self.status.set('Correct the field structure or reset it before editing quotes.');return
        if item is not None:item['evidence']=[];self.manual.add(self.field['name']);self.dirty=True;self.show_evidence()

    def save(self,accept=False,advance=False):
        if self.busy or not self.record or not self.apply_field():return
        if accept and not messagebox.askyesno('Accept finding','Accept these values? Edited fields will be stored as protected manual corrections.',parent=self.root):return
        rows=list(self.jobs.get_children());position=rows.index(self.record['job']['id']) if self.record['job']['id'] in rows else 0
        next_position=position+1 if self.filter.get() in ('all','accepted') else position
        ident=self.record['job']['id'];token=self.record['review_token'];values=copy.deepcopy(self.values);manual=set(self.manual)
        def done(record):
            self.load_record(record);self.status.set('Finding accepted.' if accept else 'Draft saved. '+('; '.join(record['issues']) if record['issues'] else 'Ready for review.'))
            if advance:self.refresh(self.offset,select_index=next_position);return
            if self.jobs.exists(ident):self.jobs.item(ident,values=(record['title'],record['job']['status']))
        self.submit(lambda:review.save(self.path,ident,values,token,manual,accept),done)

    def reset_field(self):
        if self.busy or not self.field:return
        name=self.field['name'];self.values[name]=None;self.manual.add(name);self.dirty=True;self.raw_mode.set(False);self.render_field(None);self.fields.item(name,values=(name,'Unknown','Manual edit'))

    def open_original(self):
        if not self.record or not self.record['locator']:return
        from .gui import open_source
        try:open_source({'locator':self.record['locator']})
        except Exception as error:messagebox.showerror('Open original',str(error),parent=self.root)

    def open_workflow(self):
        if hasattr(self,'workflow') and self.workflow.window.winfo_exists():self.workflow.window.lift();return
        from .metadata_controls import WorkflowPanel
        self.workflow=WorkflowPanel(self.root,self.config_path)

    def close(self):
        if self.busy:self.status.set('Finish the current save before closing.');return
        if self.record and not self.apply_field():self.dirty=True
        if self.dirty and not messagebox.askyesno('Unsaved edits','Discard unsaved edits and close?',parent=self.root):return
        if hasattr(self,'workflow') and self.workflow.window.winfo_exists() and not self.workflow.close():return
        self.root.destroy()


def launch(config_path='local-search.json'):
    root=tk.Tk()
    try:MetadataApp(root,config_path)
    except Exception as error:messagebox.showerror('Metadata review',str(error),parent=root);root.destroy();return
    root.mainloop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='local-search.json');launch(parser.parse_args().config)
