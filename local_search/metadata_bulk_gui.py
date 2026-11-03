"""Preview and confirmation for a fixed set of bulk metadata changes."""
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from tkinter.scrolledtext import ScrolledText
from . import metadata_bulk as bulk


class BulkDialog:
    def __init__(self,app,operation,ids,filters):
        self.app=app;self.operation=operation;self.ids=ids;self.filters=filters;self.plan=None;self.inflight=False;self.cancel=threading.Event();self.progress=queue.Queue()
        self.window=tk.Toplevel(app.root);self.window.title('Bulk approve' if operation=='approve' else 'Bulk find and replace');self.window.geometry('940x680');self.window.minsize(780,540)
        scope=str(len(ids))+' selected findings' if ids is not None else 'All matching filters: '+', '.join(key+'='+str(value) for key,value in filters.items() if value)
        ttk.Label(self.window,text=scope,wraplength=870,padding=12).pack(anchor='w')
        self.field=tk.StringVar();self.find=tk.StringVar();self.replacement=tk.StringVar();self.case_sensitive=tk.BooleanVar(value=False);self.whole=tk.BooleanVar(value=False)
        if operation=='replace':
            form=ttk.Frame(self.window,padding=(12,0));form.pack(fill='x');form.columnconfigure(1,weight=1)
            for row,(label,var) in enumerate([('Field',self.field),('Find',self.find),('Replace with',self.replacement)]):
                ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=(0,8),pady=4)
                widget=ttk.Combobox(form,textvariable=var,state='readonly') if row==0 else ttk.Entry(form,textvariable=var)
                widget.grid(row=row,column=1,sticky='ew')
                if row==0:self.field_box=widget
            flags=ttk.Frame(form);flags.grid(row=3,column=1,sticky='w')
            ttk.Checkbutton(flags,text='Match case',variable=self.case_sensitive).pack(side='left')
            ttk.Checkbutton(flags,text='Match whole value',variable=self.whole).pack(side='left',padx=12)
            note='Literal replacement in the chosen field’s values and scopes. Numbers/booleans match whole values. Changes are manual drafts and replace inherited quotes; approve them separately.'
        else:note='Only valid findings are approved. Stale sources, changed findings and outstanding validation errors are skipped and listed. Manual corrections use their saved override settings.'
        ttk.Label(self.window,text=note,wraplength=870,padding=12).pack(anchor='w')
        actions=ttk.Frame(self.window,padding=12);actions.pack(side='bottom',fill='x')
        self.preview_button=ttk.Button(actions,text='Preview',command=self.preview);self.preview_button.pack(side='left')
        self.apply_button=ttk.Button(actions,text='Approve previewed findings' if operation=='approve' else 'Save previewed replacements as drafts',command=self.apply,state='disabled');self.apply_button.pack(side='left',padx=8)
        self.cancel_button=ttk.Button(actions,text='Stop',command=self.cancel.set,state='disabled');self.cancel_button.pack(side='left')
        ttk.Button(actions,text='Close',command=self.close).pack(side='right')
        self.status=tk.StringVar(value='Prepare a preview before applying any changes.');ttk.Label(self.window,textvariable=self.status,wraplength=870,padding=(12,0)).pack(side='bottom',fill='x')
        self.log=ScrolledText(self.window,wrap='word',state='disabled');self.log.pack(fill='both',expand=True,padx=12,pady=8)
        for var in (self.field,self.find,self.replacement,self.case_sensitive,self.whole):var.trace_add('write',self.invalidate)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.after_id=self.window.after(100,self.poll)
        if operation=='replace':
            def loaded(names):
                self.field_box.configure(values=names)
                if names:self.field.set(app.field['name'] if app.field and app.field['name'] in names else names[0])
            app.submit(lambda:bulk.field_names(app.path),loaded)
        else:self.preview()

    def invalidate(self,*args):
        self.plan=None;self.apply_button.configure(state='disabled');self.status.set('Preview the current inputs before applying changes.')

    def write(self,text):
        self.log.configure(state='normal');self.log.delete('1.0','end');self.log.insert('end',text);self.log.configure(state='disabled')

    def start(self,work,done):
        self.inflight=True;self.cancel.clear()
        def complete(value):
            self.inflight=False;self.cancel_button.configure(state='disabled');done(value)
        self.app.submit(work,complete)
        if self.app.busy:self.cancel_button.configure(state='normal')

    def preview(self):
        if self.app.busy:return
        self.plan=None;self.apply_button.configure(state='disabled')
        field=self.field.get();find=self.find.get();replacement=self.replacement.get();case=self.case_sensitive.get();whole=self.whole.get()
        self.status.set('Preparing preview…')
        def work():return bulk.preview(self.app.path,self.operation,self.ids,self.filters,field,find,replacement,case,whole,self.cancel,self.progress.put)
        def done(plan):
            self.plan=plan
            self.status.set(f"{plan['target_count']} examined · {len(plan['entries'])} ready · {len(plan['skipped'])} skipped"+(' · preview stopped; preview again to apply' if plan['cancelled'] else ''))
            lines=['Preview only: applying is a separate step. Showing up to 100 ready findings and 100 skips; application uses the complete fixed preview set. Long values are clipped at 1,200 characters.\n']
            for entry in plan['entries'][:100]:
                lines.append(entry['title']+' ['+entry['id']+']')
                if self.operation=='replace':lines+=['Before: '+entry['before'],'After: '+entry['after'],str(entry['matches'])+' replacements']
                if entry['other_issues']:lines.append('Other fields still need review: '+'; '.join(entry['other_issues']))
            lines+=['\nSkipped:']+[item['title']+': '+item['reason'] for item in plan['skipped'][:100]]
            self.write('\n'.join(lines));self.apply_button.configure(state='normal' if plan['entries'] and not plan['cancelled'] else 'disabled')
        self.start(work,done)

    def apply(self):
        if self.app.busy or not self.plan or not self.plan['entries']:return
        plan=self.plan;count=len(plan['entries']);action='approve' if self.operation=='approve' else 'save replacements as manual drafts for'
        if not messagebox.askyesno('Apply bulk action',f'{action.capitalize()} {count} previewed findings? Each finding is checked again before saving. Completed changes remain saved if you stop the operation.',parent=self.window):return
        self.apply_button.configure(state='disabled');self.status.set('Applying previewed changes…')
        def done(result):
            self.plan=None
            self.status.set(f"{len(result['applied'])} saved · {len(result['skipped'])} skipped"+(f" · stopped, {result['remaining']} not processed" if result['cancelled'] else ''))
            self.write('Saved:\n'+'\n'.join(item['title'] for item in result['applied'][:100])+'\n\nSkipped:\n'+'\n'.join(item['title']+': '+item['reason'] for item in result['skipped'][:100]))
            self.app.dirty=False;self.app.record=None;self.app.field=None;self.app.refresh(self.app.offset)
        self.start(lambda:bulk.apply(self.app.path,plan,self.cancel,self.progress.put),done)

    def poll(self):
        progress=None
        try:
            while True:progress=self.progress.get_nowait()
        except queue.Empty:pass
        if self.inflight:
            if not self.app.busy:self.inflight=False;self.cancel_button.configure(state='disabled');self.status.set('Operation failed. See the error message, then prepare a new preview.')
            elif progress is not None:self.status.set(str(progress)+' findings processed…')
        self.after_id=self.window.after(100,self.poll)

    def close(self):
        if self.inflight:self.cancel.set();self.status.set('Stopping after the current finding. Completed changes are kept.');return
        self.window.after_cancel(self.after_id);self.window.destroy()
