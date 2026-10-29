"""Metadata sidecar: inspect, migrate, sample, extract, review and resume."""
import argparse
import hashlib
import html
import json
import os
import random
import sqlite3
import sys
import threading
import time
from pathlib import Path
from .metadata_profile import load_profile,validate_profile,profile_hash,instructions,schema_for,parse_and_validate,canonical
from .metadata_store import inspect,migrate,migrated,register,enqueue,commit_result
from .sqlite_paths import connect_readonly,network_path


def load_model(config):
    from .model import GGUF
    from .inference_lock import InferenceLease
    lease=InferenceLease();started=time.perf_counter()
    model=None
    try:
        model=GGUF(**config['model'])
        from .library_management import digest
        model.metadata_model_info={'artifact_sha256':digest(Path(config['model']['path'])),'settings':config['model'],'runtime':model.diagnostics().get('runtime')}
        model.metadata_load_seconds=time.perf_counter()-started
    except BaseException:
        try:
            if model is not None:model.close()
        finally:lease.close()
        raise
    model.metadata_lease=lease;return model


def close_model(model):
    try:model.close()
    finally:model.metadata_lease.close()


def model_key(model):
    identity=getattr(model,'metadata_model_info',{'adapter':type(model).__module__+'.'+type(model).__qualname__})
    return model_fingerprint(identity)


def model_fingerprint(identity):
    if 'settings' in identity:
        identity={**identity,'settings':{k:v for k,v in identity['settings'].items() if k not in ('path','torch_lib')}}
    return hashlib.sha256(canonical(identity).encode()).hexdigest()


def database_path(config):
    origin=Path(config['database']).resolve();options=config.get('library_sync',{})
    if not options.get('enabled') and not network_path(origin):return origin
    base=Path(options['local_root']) if options.get('local_root') else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'LocalLibrarySearch/libraries'
    ident=hashlib.sha256(os.path.normcase(str(origin)).encode()).hexdigest()[:24]
    local=base/ident/'library.db'
    if network_path(local):raise ValueError('Metadata working libraries must be on local storage')
    if not local.is_file():raise ValueError('Import/open the managed library in the app before running metadata tools')
    return local


def eligible(db,collections=(),folders=(),deduplicate=True):
    inspect(db);columns={r[1] for r in db.execute('PRAGMA table_info(sources)')}
    identity='s.source_uuid' if 'source_uuid' in columns else 'NULL'
    query=f'''SELECT s.id AS source_id,s.collection,s.source_key,s.locator,d.text_hash,{identity} AS source_uuid
      FROM sources s JOIN documents d ON d.id=s.doc_id WHERE s.status='indexed' ORDER BY s.collection,s.source_key,s.id'''
    result=[];seen=set()
    for row in db.execute(query):
        source=dict(row)
        if collections and source['collection'] not in collections:continue
        if folders:
            path=Path(source['locator'])
            if not path.is_absolute() or not any(Path(os.path.abspath(path)).is_relative_to(Path(os.path.abspath(folder))) for folder in folders):continue
        if deduplicate and source['text_hash'] in seen:continue
        seen.add(source['text_hash']);result.append(source)
    return result


def select_sample(db,n=None,seed=None,collections=(),folders=(),deduplicate=True):
    population=eligible(db,collections,folders,deduplicate)
    if n is not None and (type(n) is not int or n<1):raise ValueError('random-items must be a positive integer')
    seed=random.SystemRandom().randrange(2**63) if seed is None else seed
    selected=random.Random(seed).sample(population,min(n,len(population))) if n is not None else population
    return {'seed':seed,'eligible_count':len(population),'requested_count':n,'sampling':'document' if deduplicate else 'source','selected':selected}


def read_source(path,ident):
    db=connect_readonly(path);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN');columns={r[1] for r in db.execute('PRAGMA table_info(sources)')}
        identity='s.source_uuid' if 'source_uuid' in columns else 'NULL'
        row=db.execute(f'''SELECT s.id AS source_id,s.collection,s.source_key,s.title,s.locator,s.status,d.text_hash,
          {identity} AS source_uuid,length(d.text) AS total_chars,substr(d.text,1,200000) AS front,
          substr(d.text,max(1,length(d.text)-199999),200000) AS back,s.doc_id
          FROM sources s JOIN documents d ON d.id=s.doc_id WHERE s.id=? AND s.status='indexed' ''',(ident,)).fetchone()
        if row is None:raise ValueError('Source no longer has indexed text')
        source=dict(row);source['page_ranges']=[dict(r) for r in db.execute('SELECT page,min(start_char) AS start_char,max(end_char) AS end_char FROM locations WHERE doc_id=? GROUP BY page ORDER BY start_char',(source['doc_id'],))]
        return source
    finally:db.close()


def prepare(source,model,profile):
    from demo_metadata_extraction import excerpts_for
    system=instructions(profile);ceiling=min(profile['max_input_tokens'],model.context-model.output)
    def build(count):
        excerpts=excerpts_for(source,count)
        return [{'role':'system','content':system},{'role':'user','content':canonical({'source_id':source['source_id'],'title_for_orientation_only':source['title'],'excerpts':excerpts})}],excerpts
    low=0;high=min(profile['max_chars'],source['total_chars'],200000)
    if model.count(build(0)[0])>ceiling:raise ValueError('Field prompts/schema exceed input budget')
    while low<high:
        mid=(low+high+1)//2
        if model.count(build(mid)[0])<=ceiling:low=mid
        else:high=mid-1
    messages,excerpts=build(low)
    if not excerpts:raise ValueError('No source text fits the input budget')
    return messages,excerpts


def extract(source,model,profile):
    started=time.perf_counter();deadline=started+profile['time_limit_seconds']
    result={'source':{k:v for k,v in source.items() if k not in ('front','back','page_ranges')},'status':'failed','attempts':[],'issues':[]}
    result['model']=getattr(model,'metadata_model_info',{'adapter':type(model).__module__+'.'+type(model).__qualname__})
    result['profile_hash']=profile_hash(profile)
    try:base,excerpts=prepare(source,model,profile)
    except (ValueError,TypeError) as error:result['issues']=[str(error)];return result
    result['excerpts']=excerpts;result['evaluated_chars']=sum(len(e['text']) for e in excerpts)
    result['full_indexed_text']=result['evaluated_chars']==source['total_chars']
    prompt=base;kind='extract';repaired=False;previous=None;candidate=None;issues=[]
    external_cancel=getattr(model,'cancel_event',None)
    if external_cancel is None:model.cancel_event=threading.Event()
    timed_out=threading.Event()
    def timeout():timed_out.set();model.cancel_event.set()
    timer=threading.Timer(max(.01,deadline-time.perf_counter()),timeout);timer.daemon=True;timer.start()
    try:
        for attempt in range(profile['max_attempts']):
            if timed_out.is_set() or time.perf_counter()>=deadline:result['issues']=['Per-item time budget exhausted'];break
            if model.cancel_event.is_set():result['status']='cancelled';break
            if model.count(prompt)>min(profile['max_input_tokens'],model.context-model.output):result['issues']=['Recovery prompt exceeds input budget; no truncated repair attempted'];break
            record={'number':attempt+1,'kind':kind,'messages':prompt};call_started=time.perf_counter();parsed=None
            try:
                thinking=getattr(model,'thinking',None)
                if kind=='format_repair' and type(thinking) is bool:model.thinking=False
                try:
                    record['thinking']=getattr(model,'thinking',None)
                    response=model.complete_json(prompt,schema_for(profile)) if hasattr(model,'complete_json') else model.complete(prompt)
                finally:
                    if type(thinking) is bool:model.thinking=thinking
                raw=response['choices'][0]['text'];candidate=raw.rsplit('</think>',1)[-1].strip()
                record.update(raw_output=raw,timings=response.get('timings',{}),usage=response.get('usage',{}),json_schema_constrained=response.get('json_schema_constrained'))
                if timed_out.is_set():raise TimeoutError('Per-item time budget exhausted')
                if response['choices'][0].get('finish_reason')=='length':raise ValueError('Truncated output; retry extraction with a smaller bounded answer')
                parsed,issues=parse_and_validate(raw,profile,excerpts)
                record['issues']=issues
                if not issues:
                    changed=previous is not None and parsed!=previous and kind=='format_repair'
                    any_values=any(item is not None and item.get('value') not in ([],None,'') for item in parsed.values())
                    requires_review=repaired or changed or any(parsed[f['name']] is not None and not f.get('auto_accept',False) for f in profile['fields'])
                    result.update(metadata=parsed,issues=[],status='unknown' if not any_values else 'needs_review' if requires_review else 'extracted')
                    if changed:result['review_reason']='Formatting repair changed parsed values'
                    elif repaired:result['review_reason']='Repaired JSON requires review; preservation cannot be proven for malformed input'
                    record['outcome']='valid';result['attempts'].append(dict(record,seconds=time.perf_counter()-call_started));break
                previous=parsed
                prompt=base+[{'role':'user','content':'Correct these validation errors using only the original excerpts: '+canonical(issues)}];kind='targeted_retry'
                result.update(metadata=parsed,issues=issues,status='needs_review')
            except Exception as error:
                record.update(error_type=type(error).__name__,error=str(error),outcome='rejected');result['issues']=[str(error)]
                if timed_out.is_set():result['status']='failed'
                elif candidate is not None and 'Truncated output' not in str(error):
                    repaired=True;kind='format_repair'
                    prompt=[{'role':'system','content':'Repair JSON formatting only. Preserve extracted values, evidence quotes and IDs. Add no facts. Unsupported fields may be null. Return one JSON object without thinking or Markdown.'},
                            {'role':'user','content':canonical({'schema':schema_for(profile),'candidate':candidate,'validation_error':str(error)})}]
                else:
                    kind='targeted_retry';prompt=base+[{'role':'user','content':'The last attempt was incomplete. Return a concise complete object; use null for unsupported fields.'}]
            result['attempts'].append(dict(record,seconds=time.perf_counter()-call_started))
            if timed_out.is_set():break
        if result['status']=='needs_review' and result.get('issues'):result['review_reason']='Validation issues remain; acceptance is blocked'
    finally:
        timer.cancel()
        if external_cancel is None:del model.cancel_event
        result['total_seconds']=time.perf_counter()-started
        result['raw_output']=result['attempts'][-1].get('raw_output','') if result['attempts'] else ''
    return result


def write_item(directory,index,result,profile,dry_run=True):
    from demo_metadata_extraction import write_report
    location=directory/('item-'+str(index));run={'dry_run':dry_run,'source_ids':[result['source']['source_id']],'system_prompt':instructions(profile),'results':[dict(result,validation_issues=result.get('issues',[]))]}
    write_report(location,run)
    return {'source_id':result['source']['source_id'],'status':result['status'],'attempts':len(result['attempts']),'first_attempt_valid':bool(result['attempts'] and result['attempts'][0].get('outcome')=='valid'),'format_repair_used':any(a['kind']=='format_repair' for a in result['attempts']),'seconds':result.get('total_seconds',0),'report':location.name+'/report.html'}


def write_summary(directory,manifest,summaries):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    (directory/'results.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),'utf-8')
    import statistics
    times=sorted(item['seconds'] for item in summaries)
    metrics={'items':len(summaries),'median_seconds':statistics.median(times) if times else None,'p95_seconds':times[min(len(times)-1,max(0,__import__('math').ceil(len(times)*.95)-1))] if times else None,
             'first_attempt_valid':sum(item['first_attempt_valid'] for item in summaries),'format_repair_items':sum(item['format_repair_used'] for item in summaries),
             'failed_items':sum(item['status']=='failed' for item in summaries),'unknown_items':sum(item['status']=='unknown' for item in summaries)}
    (directory/'metrics.json').write_text(json.dumps(metrics,indent=2),'utf-8')
    parts=['<!doctype html><meta charset="utf-8"><title>Metadata run</title><h1>Metadata extraction review</h1>',
           '<p>'+html.escape('Dry run: '+str(manifest['dry_run'])+'; eligible '+str(manifest.get('eligible_count','queued'))+'; selected '+str(len(manifest['selected'])))+'</p>','<pre>'+html.escape(json.dumps(metrics,indent=2))+'</pre>','<ul>']
    for item in summaries:parts.append('<li><a href="'+html.escape(item['report'],quote=True)+'">Source '+str(item['source_id'])+'</a>: '+html.escape(item['status'])+', '+str(item['attempts'])+' attempts, '+str(round(item['seconds'],2))+'s</li>')
    (directory/'report.html').write_text('\n'.join(parts+['</ul>']),'utf-8')


def run_sample(path,model,profile,directory,selection,dry_run=True,library=None,force=False):
    directory=Path(directory)
    if directory.exists() and any(directory.iterdir()):raise ValueError('Choose a new empty output directory to preserve prior reports')
    directory.mkdir(parents=True,exist_ok=True)
    manifest={**selection,'dry_run':dry_run,'profile_hash':profile_hash(profile),'profile':profile,'database':str(Path(path).resolve()),'model':getattr(model,'metadata_model_info',None),'model_load_seconds':getattr(model,'metadata_load_seconds',None)}
    summaries=[];write_summary(directory,manifest,summaries)
    key=None
    if not dry_run:
        if library is None or not migrated(library.db):raise ValueError('Migrate the library before a write run')
        with library.db:key=register(library.db,profile)
    for index,selected in enumerate(selection['selected'],1):
        try:
            source=read_source(path,selected['source_id'])
            if any(source[k]!=selected[k] for k in ('collection','source_key','locator','text_hash')):raise ValueError('Sample source changed; rerun selection before applying')
            if selected.get('source_uuid') is not None and source['source_uuid']!=selected['source_uuid']:raise ValueError('Sample source identity changed; rerun selection')
        except ValueError as error:
            result={'source':dict(selected,title='Source '+str(selected['source_id']),total_chars=0),'status':'stale','attempts':[],'issues':[str(error)]}
            summaries.append(write_item(directory,index,result,profile,dry_run));write_summary(directory,manifest,summaries);continue
        job=None
        if library is not None and not dry_run:
            with library.db:
                job_id=enqueue(library.db,source['source_id'],key,force)
                job=dict(library.db.execute('SELECT * FROM metadata_jobs WHERE id=?',(job_id,)).fetchone())
                if job['status'] not in ('pending','failed','running') and job.get('model_hash')==model_key(model):continue
                job['model_hash']=model_key(model)
                library.db.execute("UPDATE metadata_jobs SET status='running',model_hash=?,updated_at=? WHERE id=?",(job['model_hash'],time.time(),job_id))
        result=extract(source,model,profile)
        if job:result['status']=commit_result(library,job,result,profile)
        summaries.append(write_item(directory,index,result,profile,dry_run));write_summary(directory,manifest,summaries)
        print('Source '+str(source['source_id'])+': '+result['status']+' ('+str(len(result['attempts']))+' attempts)',file=sys.stderr,flush=True)
    return {'dry_run':dry_run,'items':len(summaries),'report':str(directory/'report.html'),'results':summaries}


def apply_report(library,directory,apply=False,accept_reviewed=False,force=False):
    directory=Path(directory);manifest=json.loads((directory/'manifest.json').read_text('utf-8'))
    if not manifest.get('dry_run'):raise ValueError('Choose a dry-run report')
    if Path(manifest['database']).resolve()!=library.path.resolve():raise ValueError('Report belongs to a different working library')
    profile=validate_profile(manifest['profile'])
    if profile_hash(profile)!=manifest['profile_hash']:raise ValueError('Profile/prompt engine changed; create a fresh preview')
    if apply and not migrated(library.db):raise ValueError('Migrate the library before applying a report')
    outcomes=[]
    for index,selected in enumerate(manifest['selected'],1):
        report=json.loads((directory/('item-'+str(index))/'results.json').read_text('utf-8'))['results'][0]
        if report['status'] not in ('extracted','needs_review','unknown') or report.get('issues'):
            outcomes.append({'source_id':selected['source_id'],'status':'skipped_invalid'});continue
        try:current=read_source(library.path,selected['source_id'])
        except ValueError:
            outcomes.append({'source_id':selected['source_id'],'status':'stale'});continue
        if any(current[k]!=selected[k] for k in ('collection','source_key','locator','text_hash')) or selected.get('source_uuid') is not None and selected['source_uuid']!=current['source_uuid']:
            outcomes.append({'source_id':selected['source_id'],'status':'stale'});continue
        for excerpt in report.get('excerpts',[]):
            actual=library.db.execute('SELECT substr(text,?,?) FROM documents WHERE id=?',(excerpt['start_char']+1,len(excerpt['text']),current['doc_id'])).fetchone()[0]
            if actual!=excerpt['text']:raise ValueError('Review excerpt no longer matches the indexed source')
        _,issues=parse_and_validate(canonical(report['metadata']),profile,report.get('excerpts',[]))
        if issues:raise ValueError('Review metadata fails revalidation: '+canonical(issues))
        outcome={'source_id':selected['source_id'],'status':'ready'}
        if apply:
            with library.db:
                key=register(library.db,profile);job_id=enqueue(library.db,current['source_id'],key)
                job=dict(library.db.execute('SELECT * FROM metadata_jobs WHERE id=?',(job_id,)).fetchone())
                if job['status'] not in ('pending','failed','running') and not force:
                    outcomes.append(dict(outcome,status='already_processed',job_id=job_id));continue
                job['model_hash']=model_fingerprint(report.get('model',{}))
                library.db.execute('UPDATE metadata_jobs SET model_hash=? WHERE id=?',(job['model_hash'],job_id))
            if accept_reviewed and report['status']!='unknown':report['status']='accepted'
            report['applied_from_dry_run']=True
            outcome.update(status=commit_result(library,job,report,profile),job_id=job_id)
        outcomes.append(outcome)
    return {'applied':apply,'results':outcomes}


def main(argv=None):
    from .__main__ import configuration
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='local-search.json')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('inspect')
    sub.add_parser('gui')
    migration=sub.add_parser('migrate');migration.add_argument('--apply',action='store_true')
    profile_parser=sub.add_parser('profile');profile_parser.add_argument('--profile')
    run=sub.add_parser('run');run.add_argument('--profile');run.add_argument('--dry-run',action='store_true');run.add_argument('--random-items',type=int,default=20);run.add_argument('--all',action='store_true');run.add_argument('--seed',type=int);run.add_argument('--collection',action='append',default=[]);run.add_argument('--folder',action='append',default=[]);run.add_argument('--source-sampling',action='store_true');run.add_argument('--manifest');run.add_argument('--output-dir',required=True);run.add_argument('--force',action='store_true')
    queue=sub.add_parser('queue');queue.add_argument('--profile');queue.add_argument('--random-items',type=int,default=20);queue.add_argument('--all',action='store_true');queue.add_argument('--seed',type=int);queue.add_argument('--collection',action='append',default=[]);queue.add_argument('--folder',action='append',default=[]);queue.add_argument('--apply',action='store_true')
    policy=sub.add_parser('policy');policy.add_argument('--collection',required=True);policy.add_argument('--profile');policy.add_argument('--mode',choices=('off','missing','changed'),required=True)
    jobs=sub.add_parser('jobs');jobs.add_argument('--status');jobs.add_argument('--limit',type=int,default=50)
    review=sub.add_parser('review');review.add_argument('--job',required=True);review.add_argument('--accept',action='store_true')
    work=sub.add_parser('work');work.add_argument('--limit',type=int,default=20);work.add_argument('--retry-failed',action='store_true');work.add_argument('--output-dir',required=True)
    values=sub.add_parser('values');values.add_argument('--source-id',type=int,required=True)
    override=sub.add_parser('set-field');override.add_argument('--source-id',type=int,required=True);override.add_argument('--profile');override.add_argument('--field',required=True);override.add_argument('--value-json',required=True)
    sub.add_parser('validate')
    backup_parser=sub.add_parser('backup');backup_parser.add_argument('--output',required=True)
    restore_parser=sub.add_parser('restore');restore_parser.add_argument('--backup',required=True);restore_parser.add_argument('--apply',action='store_true')
    apply_parser=sub.add_parser('apply-report');apply_parser.add_argument('--report-dir',required=True);apply_parser.add_argument('--apply',action='store_true');apply_parser.add_argument('--accept-reviewed',action='store_true');apply_parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='gui':
        from .metadata_gui import launch
        launch(args.config);return
    if args.command=='profile':
        profile=load_profile(args.profile);print(json.dumps({'profile':profile,'hash':profile_hash(profile),'prompt':instructions(profile),'schema':schema_for(profile)},ensure_ascii=False,indent=2));return
    config=configuration(args.config);path=database_path(config)
    if args.command in ('apply-report','restore') and not args.apply:
        from types import SimpleNamespace
        from .metadata_store import restore
        db=connect_readonly(path);db.row_factory=sqlite3.Row
        try:
            inspect(db);library=SimpleNamespace(path=path,db=db)
            result=apply_report(library,args.report_dir,False,args.accept_reviewed,args.force) if args.command=='apply-report' else restore(library,args.backup,False)
        finally:db.close()
        print(json.dumps(result,indent=2));return
    if args.command in ('validate','backup'):
        db=connect_readonly(path)
        try:
            inspect(db)
            if args.command=='validate':result={'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'foreign_key_errors':db.execute('PRAGMA foreign_key_check').fetchall()}
            else:
                destination=Path(args.output).resolve()
                if network_path(destination):raise ValueError('Create backups locally; use library-sync to publish a closed snapshot to the share')
                if destination.exists():raise ValueError('Choose a new backup filename; existing files are not overwritten')
                destination.parent.mkdir(parents=True,exist_ok=True);target=sqlite3.connect(destination)
                try:db.backup(target)
                finally:target.close()
                result={'backup':str(destination)}
        finally:db.close()
        print(json.dumps(result,indent=2));return
    if args.command=='inspect' or args.command=='migrate' and not args.apply:
        db=connect_readonly(path)
        try:result=inspect(db);result['migration_required']=result['metadata_schema'] is None
        finally:db.close()
        print(json.dumps(result,indent=2));return
    if args.command=='queue' and not args.apply:
        db=connect_readonly(path);db.row_factory=sqlite3.Row
        try:result=select_sample(db,None if args.all else args.random_items,args.seed,args.collection,args.folder)
        finally:db.close()
        result['applied']=False;print(json.dumps(result,indent=2));return
    if args.command=='run' and args.dry_run:
        profile=load_profile(args.profile);db=connect_readonly(path);db.row_factory=sqlite3.Row
        try:
            if args.manifest:
                manifest=json.loads(Path(args.manifest).read_text('utf-8'));selection={k:v for k,v in manifest.items() if k not in ('dry_run','profile','profile_hash','database')}
                if Path(manifest['database']).resolve()!=path.resolve():raise ValueError('Manifest belongs to a different working library')
                if manifest['profile_hash']!=profile_hash(profile):raise ValueError('Sample profile changed; rerun preview')
            else:selection=select_sample(db,None if args.all else args.random_items,args.seed,args.collection,args.folder,not args.source_sampling)
        finally:db.close()
        model=load_model(config) if selection['selected'] else None
        try:result=run_sample(path,model,profile,args.output_dir,selection)
        finally:
            if model is not None:close_model(model)
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    from .library_management import configured_library
    with configured_library(config) as library:
        if args.command=='migrate':result=migrate(library,True)
        elif args.command=='restore':
            from .metadata_store import restore
            result=restore(library,args.backup,args.apply)
        elif args.command=='apply-report':result=apply_report(library,args.report_dir,args.apply,args.accept_reviewed,args.force)
        else:
            if not migrated(library.db):raise ValueError('Run metadata migrate --apply first')
            if args.command=='policy':
                with library.db:
                    if args.mode=='off':library.db.execute('DELETE FROM metadata_policies WHERE collection=?',(args.collection,))
                    else:
                        key=register(library.db,load_profile(args.profile))
                        library.db.execute('INSERT INTO metadata_policies VALUES(?,?,?) ON CONFLICT(collection) DO UPDATE SET profile_hash=excluded.profile_hash,mode=excluded.mode',(args.collection,key,args.mode))
                result={'collection':args.collection,'mode':args.mode}
            elif args.command=='queue':
                profile=load_profile(args.profile);selection=select_sample(library.db,None if args.all else args.random_items,args.seed,args.collection,args.folder)
                with library.db:
                    key=register(library.db,profile)
                    jobs=[enqueue(library.db,item['source_id'],key) for item in selection['selected']]
                result={'queued_or_existing':len(jobs),'job_ids':jobs,'profile_hash':key,'seed':selection['seed']}
            elif args.command in ('values','set-field'):
                from .metadata_store import current_source
                source=current_source(library.db,args.source_id)
                if not source:raise ValueError('Unknown source')
                if args.command=='set-field':
                    from .metadata_profile import unique_object
                    profile=load_profile(args.profile);field=next((f for f in profile['fields'] if f['name']==args.field),None)
                    if field is None:raise ValueError('Field is not defined by this profile')
                    value=json.loads(args.value_json,object_pairs_hook=unique_object)
                    checked=dict(field,evidence=False);single=validate_profile({'name':'manual-validation','fields':[checked]})
                    _,issues=parse_and_validate(canonical({args.field:value}),single,[])
                    if issues:raise ValueError('; '.join(issues))
                    with library.db:
                        key=register(library.db,profile)
                        library.db.execute('INSERT INTO metadata_overrides VALUES(?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(source['source_uuid'],args.field,canonical(value),time.time()))
                        library.db.execute('INSERT INTO metadata_values VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET content_hash=excluded.content_hash,profile_hash=excluded.profile_hash,value=excluded.value,status=excluded.status,origin=excluded.origin,job_id=excluded.job_id,updated_at=excluded.updated_at',(source['source_uuid'],args.field,source['text_hash'],key,canonical(value),'accepted','manual','manual',time.time()))
                result=[dict(row,stale=row['content_hash']!=source['text_hash']) for row in library.db.execute('SELECT * FROM metadata_values WHERE source_uuid=?',(source['source_uuid'],))]
            elif args.command=='jobs':
                where='WHERE status=?' if args.status else '';params=[args.status] if args.status else []
                result=[dict(r) for r in library.db.execute('SELECT id,source_id,source_uuid,content_hash,profile_hash,status,issues,updated_at FROM metadata_jobs '+where+' ORDER BY created_at LIMIT ?',params+[max(1,min(1000,args.limit))])]
            elif args.command=='review':
                row=library.db.execute('SELECT * FROM metadata_jobs WHERE id=?',(args.job,)).fetchone()
                if row is None:raise ValueError('Unknown job')
                result=dict(row)
                definition=library.db.execute('SELECT definition,prompt FROM metadata_profiles WHERE hash=?',(result['profile_hash'],)).fetchone()
                result['profile']=json.loads(definition[0]);result['resolved_prompt']=definition[1]
                if args.accept:
                    if result['status']!='needs_review' or json.loads(result['issues']):raise ValueError('Only valid needs-review results can be accepted')
                    from .metadata_review import fetch,save
                    record=fetch(library.path,args.job)
                    save(library.path,args.job,record['metadata'],record['review_token'],record['manual_fields'],True)
                    result['status']='accepted'
            elif args.command in ('run','work'):
                from .model import GGUF
                if args.command=='run':
                    profile=load_profile(args.profile)
                    if args.manifest:
                        manifest=json.loads(Path(args.manifest).read_text('utf-8'))
                        if Path(manifest['database']).resolve()!=library.path.resolve():raise ValueError('Manifest belongs to a different working library')
                        if manifest['profile_hash']!=profile_hash(profile):raise ValueError('Sample profile changed')
                        selection={k:v for k,v in manifest.items() if k not in ('dry_run','profile','profile_hash','database')}
                    else:selection=select_sample(library.db,None if args.all else args.random_items,args.seed,args.collection,args.folder,not args.source_sampling)
                    model=load_model(config) if selection['selected'] else None
                    try:result=run_sample(library.path,model,profile,args.output_dir,selection,False,library,args.force)
                    finally:
                        if model is not None:close_model(model)
                else:
                    result=run_queued(library,config,args)
        print(json.dumps(result,ensure_ascii=False,indent=2))


def run_queued(library,config,args):
    from .model import GGUF
    if not 1<=args.limit<=10000:raise ValueError('limit must be 1..10000')
    statuses=['pending','running']+(['failed'] if args.retry_failed else [])
    jobs=[dict(r) for r in library.db.execute('SELECT * FROM metadata_jobs WHERE status IN ('+','.join('?' for _ in statuses)+') ORDER BY created_at LIMIT ?',statuses+[args.limit])]
    if not jobs:return {'items':0}
    directory=Path(args.output_dir)
    if directory.exists() and any(directory.iterdir()):raise ValueError('Choose a new empty output directory to preserve prior reports')
    directory.mkdir(parents=True,exist_ok=True);model=load_model(config);summaries=[]
    try:
        for index,job in enumerate(jobs,1):
            profile=validate_profile(json.loads(library.db.execute('SELECT definition FROM metadata_profiles WHERE hash=?',(job['profile_hash'],)).fetchone()[0]))
            if profile_hash(profile)!=job['profile_hash']:
                with library.db:library.db.execute("UPDATE metadata_jobs SET status='failed',issues=?,updated_at=? WHERE id=?",(canonical(['Prompt engine changed; register the updated profile and requeue explicitly']),time.time(),job['id']))
                continue
            from .metadata_store import current_source
            current=current_source(library.db,job['source_id'])
            if not current or current['source_uuid']!=job['source_uuid'] or current['text_hash']!=job['content_hash']:
                with library.db:library.db.execute("UPDATE metadata_jobs SET status='stale',updated_at=? WHERE id=?",(time.time(),job['id']))
                continue
            with library.db:library.db.execute("UPDATE metadata_jobs SET status='running',updated_at=? WHERE id=?",(time.time(),job['id']))
            job['model_hash']=model_key(model)
            with library.db:library.db.execute('UPDATE metadata_jobs SET model_hash=? WHERE id=?',(job['model_hash'],job['id']))
            source=read_source(library.path,job['source_id']);result=extract(source,model,profile)
            result['status']=commit_result(library,job,result,profile);summaries.append(write_item(directory,index,result,profile,False))
    finally:close_model(model)
    write_summary(directory,{'dry_run':False,'selected':[{'source_id':job['source_id']} for job in jobs]},summaries)
    return {'items':len(summaries),'report':str(directory/'report.html'),'results':summaries}


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as error:print(type(error).__name__+': '+str(error),file=sys.stderr);raise SystemExit(1)
