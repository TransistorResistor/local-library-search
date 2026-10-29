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
    value=item.get('value')
    if isinstance(value,list):return '; '.join(str(v.get('value',v.get('scope',''))) if isinstance(v,dict) else str(v) for v in value)
    return str(value)


class MetadataApp:
    def __init__(self,root,config_path):
        from .__main__ import configuration
        self.root=root;self.config_path=config_path;self.path=database_path(configuration(config_path))
        self.record=None;self.values={};self.manual=set();self.field=None;self.busy=False;self.dirty=False;self.results=queue.Queue();self.list_items=[]
        root.title('Metadata review');root.geometry('1180x800');root.minsize(900,650)
        self.status=tk.StringVar(value='Loading findings…');self.filter=tk.StringVar(value='needs_review')
        bar=ttk.Frame(root,padding=12);bar.pack(fill='x')
        ttk.Label(bar,text='Metadata review',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Combobox(bar,textvariable=self.filter,values=('needs_review','accepted','extracted','unknown','failed','all'),state='readonly',width=16).pack(side='right')
        ttk.Button(bar,text='Refresh',command=self.refresh).pack(side='right',padx=8)
        panes=ttk.Panedwindow(root,orient='horizontal');panes.pack(fill='both',expand=True,padx=12)
        left=ttk.Frame(panes);right=ttk.Frame(panes,padding=(12,0));panes.add(left,weight=1);panes.add(right,weight=3)
        self.jobs=ttk.Treeview(left,columns=('title','status'),show='headings',selectmode='browse')
        self.jobs.heading('title',text='Report');self.jobs.heading('status',text='Status');self.jobs.column('title',width=240);self.jobs.column('status',width=100)
        self.jobs.pack(fill='both',expand=True);self.jobs.bind('<<TreeviewSelect>>',self.select_job)
        self.title=tk.StringVar(value='Choose a finding');ttk.Label(right,textvariable=self.title,font=('Segoe UI',12,'bold'),wraplength=720).pack(anchor='w',pady=(0,8))
        self.fields=ttk.Treeview(right,columns=('field','value','state'),show='headings',height=6,selectmode='browse')
        for name,label,width in [('field','Field',150),('value','Finding',410),('state','Review',110)]:self.fields.heading(name,text=label);self.fields.column(name,width=width)
        self.fields.pack(fill='x');self.fields.bind('<<TreeviewSelect>>',self.select_field)
        self.editor=ttk.LabelFrame(right,text='Edit finding',padding=8);self.editor.pack(fill='x',pady=8)
        self.unknown=tk.BooleanVar();ttk.Checkbutton(self.editor,text='Unknown / no value',variable=self.unknown).pack(anchor='w')
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
        self.list_values=[];self.help=tk.StringVar();ttk.Label(self.editor,textvariable=self.help,wraplength=700).pack(anchor='w',pady=4)
        ttk.Button(self.editor,text='Apply field edit',command=self.apply_field).pack(anchor='w')
        self.details=ttk.Notebook(right);self.details.pack(fill='both',expand=True)
        evidence_page=ttk.Frame(self.details);attempt_page=ttk.Frame(self.details);self.details.add(evidence_page,text='Evidence');self.details.add(attempt_page,text='Attempts / prompt')
        self.evidence=ScrolledText(evidence_page,wrap='word',height=8,state='disabled');self.evidence.pack(fill='both',expand=True)
        self.attempts=ScrolledText(attempt_page,wrap='word',height=8,state='disabled');self.attempts.pack(fill='both',expand=True)
        self.quote=tk.StringVar();self.excerpt=tk.StringVar()
        evidence_bar=ttk.Frame(evidence_page);evidence_bar.pack(fill='x',pady=4)
        self.excerpt_box=ttk.Combobox(evidence_bar,textvariable=self.excerpt,state='readonly',width=5);self.excerpt_box.pack(side='left')
        ttk.Entry(evidence_bar,textvariable=self.quote).pack(side='left',fill='x',expand=True,padx=4)
        ttk.Button(evidence_bar,text='Use quote',command=self.set_quote).pack(side='left');ttk.Button(evidence_bar,text='Clear quotes',command=self.clear_quotes).pack(side='left',padx=4)
        actions=ttk.Frame(root,padding=12);actions.pack(fill='x')
        self.save_button=ttk.Button(actions,text='Save draft',command=lambda:self.save(False));self.save_button.pack(side='left')
        self.accept_button=ttk.Button(actions,text='Accept finding',command=lambda:self.save(True));self.accept_button.pack(side='left',padx=8)
        ttk.Label(actions,text='Edited fields become protected manual corrections when accepted.',wraplength=600).pack(side='left',padx=12)
        ttk.Label(root,textvariable=self.status,padding=(12,4),wraplength=1100).pack(fill='x')
        self.filter.trace_add('write',lambda *args:self.refresh());root.after(80,self.poll);root.protocol('WM_DELETE_WINDOW',self.close);self.refresh()

    def submit(self,work,done):
        if self.busy:return
        self.busy=True;self.save_button.configure(state='disabled');self.accept_button.configure(state='disabled')
        def run():
            try:self.results.put((work(),None,done))
            except Exception as error:self.results.put((None,str(error),done))
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        try:
            result,error,done=self.results.get_nowait();self.busy=False
            self.save_button.configure(state='normal');self.accept_button.configure(state='normal')
            if error:self.status.set(error);messagebox.showerror('Metadata review',error,parent=self.root)
            else:done(result)
        except queue.Empty:pass
        self.root.after(80,self.poll)

    def refresh(self):
        if self.busy:return
        if self.record and not self.apply_field():return
        if self.dirty and not messagebox.askyesno('Unsaved edits','Discard unsaved edits and refresh?',parent=self.root):return
        status=self.filter.get()
        def done(items):
            self.record=None;self.field=None;self.values={};self.fields.delete(*self.fields.get_children());self.title.set('Choose a finding')
            self.list_items=items;self.jobs.delete(*self.jobs.get_children())
            for item in items:self.jobs.insert('','end',iid=item['id'],values=(item['title'] or 'Removed source',item['status']))
            self.status.set(str(len(items))+' findings · '+str(self.path));self.dirty=False
        self.submit(lambda:review.jobs(self.path,status),done)

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
        if not self.record or not selected:return
        if self.field and self.field['name']!=selected[0] and not self.apply_field():
            self.fields.selection_set(self.field['name']);return
        self.field=next(f for f in self.record['profile']['fields'] if f['name']==selected[0]);item=self.values.get(self.field['name']);self.unknown.set(item is None)
        value=item.get('value') if item else '';kind=self.field['type'];self.value_text.delete('1.0','end')
        self.value_text.insert('end','\n'.join(value) if kind=='string_list' and isinstance(value,list) else str(value).lower() if kind=='boolean' else '' if kind in ('date_list','interval_list') else str(value))
        self.precision.set(item.get('precision','day') if item else 'day');self.scope.set(item.get('scope','') if item else '')
        self.list_values=copy.deepcopy(value) if kind in ('date_list','interval_list') and isinstance(value,list) else [];self.draw_list()
        if kind in ('date_list','interval_list'):self.list_frame.pack(fill='x',pady=4);self.value_text.pack_forget()
        else:self.list_frame.pack_forget();self.value_text.pack(fill='x',pady=4,before=self.scalar_detail)
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
        if not self.field or not self.record:return True
        name=self.field['name'];kind=self.field['type'];old=self.values.get(name)
        try:
            if self.unknown.get():item=None
            else:
                text=self.value_text.get('1.0','end-1c').strip()
                value=copy.deepcopy(self.list_values) if kind in ('date_list','interval_list') else [line.strip() for line in text.splitlines() if line.strip()] if kind=='string_list' else int(text) if kind=='integer' else float(text) if kind=='number' else {'true':True,'false':False}[text.lower()] if kind=='boolean' else text
                item={'value':value,'evidence':copy.deepcopy(old.get('evidence',[])) if old else []}
                if kind=='date':item['precision']=self.precision.get()
                if self.field.get('scoped'):item['scope']=self.scope.get().strip()
            if canonical(item)!=canonical(old):
                self.values[name]=item;self.manual.add(name);self.dirty=True
                self.fields.item(name,values=(name,summary(item),'Manual edit'))
            return True
        except (ValueError,KeyError) as error:self.status.set('Check the value for '+name+': '+str(error));return False

    def show_evidence(self):
        if not self.field:return
        item=self.values.get(self.field['name']) or {};quotes=item.get('evidence',[])
        text='Supporting quotes for this field:\n'+json.dumps(quotes,ensure_ascii=False,indent=2)+'\n\nOriginal excerpts:\n'
        for excerpt in self.record['excerpts']:text+='\n'+excerpt['excerpt_id']+' · pages '+str(excerpt.get('pages',[]))+'\n'+excerpt['text']+'\n'
        self._text(self.evidence,text);ids=[e['excerpt_id'] for e in self.record['excerpts']];self.excerpt_box.configure(values=ids)
        if ids:self.excerpt.set(ids[0])

    def set_quote(self):
        if not self.apply_field() or not self.field:return
        item=self.values.get(self.field['name'])
        if item is None:self.status.set('Set a value before adding evidence.');return
        text=next((e['text'] for e in self.record['excerpts'] if e['excerpt_id']==self.excerpt.get()),'')
        if not self.quote.get().strip() or self.quote.get() not in text:self.status.set('The quote must exactly match the selected excerpt.');return
        item['evidence']=[{'excerpt_id':self.excerpt.get(),'quote':self.quote.get()}];self.manual.add(self.field['name']);self.dirty=True;self.show_evidence()

    def clear_quotes(self):
        if not self.apply_field() or not self.field:return
        item=self.values.get(self.field['name'])
        if item is not None:item['evidence']=[];self.manual.add(self.field['name']);self.dirty=True;self.show_evidence()

    def save(self,accept=False):
        if self.busy or not self.record or not self.apply_field():return
        if accept and not messagebox.askyesno('Accept finding','Accept these values? Edited fields will be stored as protected manual corrections.',parent=self.root):return
        ident=self.record['job']['id'];token=self.record['review_token'];values=copy.deepcopy(self.values);manual=set(self.manual)
        def done(record):
            self.load_record(record);self.status.set('Finding accepted.' if accept else 'Draft saved. '+('; '.join(record['issues']) if record['issues'] else 'Ready for review.'))
            if self.jobs.exists(ident):self.jobs.item(ident,values=(record['title'],record['job']['status']))
        self.submit(lambda:review.save(self.path,ident,values,token,manual,accept),done)

    def close(self):
        if self.busy:self.status.set('Finish the current save before closing.');return
        if self.record and not self.apply_field():return
        if self.dirty and not messagebox.askyesno('Unsaved edits','Discard unsaved edits and close?',parent=self.root):return
        self.root.destroy()


def launch(config_path='local-search.json'):
    root=tk.Tk()
    try:MetadataApp(root,config_path)
    except Exception as error:messagebox.showerror('Metadata review',str(error),parent=root);root.destroy();return
    root.mainloop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='local-search.json');launch(parser.parse_args().config)
