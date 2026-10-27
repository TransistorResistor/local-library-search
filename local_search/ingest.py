"""Read-only clipper and recursive PDF ingestion; write only the owned library."""
import hashlib
import importlib.metadata
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from .store import sha, utc_now
from .progress import Progress,FailureLog
from .sqlite_paths import connect_readonly,network_path

EXTRACT_VERSION='pdf-text-v1'


class RefreshCancelled(Exception):
    pass


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():raise RefreshCancelled('Refresh cancelled; deletion reconciliation skipped')


class Inventory:
    """Bounded-memory manifest, spilled to application-owned scratch storage."""
    def __init__(self,scratch=None):
        self.stream=tempfile.SpooledTemporaryFile(mode='w+t',encoding='utf-8',max_size=4*1024*1024,dir=scratch)
        self.count=0;self.scan_errors=[]
    def add(self,value):self.stream.write(json.dumps(value,ensure_ascii=False)+'\n');self.count+=1
    def __iter__(self):
        self.stream.seek(0)
        for line in self.stream:yield json.loads(line)
    def close(self):self.stream.close()


def discover_folder(config,inventory,on_progress=None,on_failure=None,cancel_event=None):
    root=Path(config['path']).expanduser().resolve()
    if not root.is_dir():raise ValueError(f'Folder unavailable; retained index: {root}')
    def onerror(error):
        inventory.scan_errors.append(str(error))
        if on_failure:on_failure(config['name'],getattr(error,'filename',None) or root,error,'SCAN ERROR')
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=onerror):
        check_cancel(cancel_event)
        if on_progress:on_progress(folder,inventory.count)
        dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink() and not (hasattr(Path(folder)/d,'is_junction') and (Path(folder)/d).is_junction())]
        for name in sorted(files):
            check_cancel(cancel_event)
            path=Path(folder)/name
            if path.suffix.lower()=='.pdf' and not path.is_symlink():inventory.add(str(path))
            if on_progress:on_progress(folder,inventory.count)


def file_hash(path,cancel_event=None):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            check_cancel(cancel_event);h.update(block)
    return h.hexdigest()


def date(value):
    if value is None or value=='':return None
    try:
        if isinstance(value,(int,float)):
            result=datetime.fromtimestamp(value,timezone.utc)
        else:
            result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if result.tzinfo is None:result=result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError,OverflowError,OSError):return None


def text_quality(text,min_words=20,ascii_ratio=.8):
    letters=[c for c in text if c.isalpha()]
    words=re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*",text)
    ascii_letters=sum(c.isascii() for c in letters)/max(1,len(letters))
    printable=sum(c.isprintable() or c.isspace() for c in text)/max(1,len(text))
    normal=[w for w in words if 2<=len(w)<=30]
    vowels=sum(bool(re.search('[aeiouy]',w,re.I)) for w in normal)/max(1,len(normal))
    common={'the','of','and','to','in','a','is','for','with','on','by','as','at','from','that','this','are','was','or','an','be','it','we','our','has','have','can','not'}
    common_ratio=sum(w.lower() in common for w in words)/max(1,len(words))
    reasons=[]
    if len(words)<min_words:reasons.append('too little English text / possible scanned page')
    if ascii_letters<ascii_ratio:reasons.append('non-ASCII-dominant text')
    if printable<.98 or '\ufffd' in text or '(cid:' in text:reasons.append('damaged character mapping')
    if len(normal)/max(1,len(words))<.6 or vowels<.55:reasons.append('fragmented or gibberish-looking words')
    if len(words)>=40 and common_ratio<.025:reasons.append('low prose plausibility')
    return dict(accepted=not reasons,reasons=reasons,ascii_letter_ratio=round(ascii_letters,3),
                printable_ratio=round(printable,3),words=len(words),common_word_ratio=round(common_ratio,3))


def extract_pdf(path,options):
    from pypdf import PdfReader
    reader=PdfReader(path,strict=False)
    if reader.is_encrypted and not reader.decrypt(''):return {'segments':[],'reason':'encrypted PDF requires password'}
    if len(reader.pages)>options.get('max_pages',2000):return {'segments':[],'reason':'page limit exceeded'}
    segments=[];quality=[]
    for number,page in enumerate(reader.pages,1):
        try:
            text=(page.extract_text() or '').replace('\x00','').strip()
            review=text_quality(text,options.get('min_words',20),options.get('ascii_ratio',.8))
        except Exception as e:
            review={'accepted':False,'reasons':['page extraction failed: '+type(e).__name__]};text=''
        quality.append(dict(page=number,**review))
        if review['accepted']:segments.append({'page':number,'text':text})
    return dict(segments=segments,quality=quality,pages=len(reader.pages),
                reason=None if segments else 'no pages passed text quality',
                pdf_creation_date=str((reader.metadata or {}).get('/CreationDate','')),
                title=str((reader.metadata or {}).get('/Title',''))[:500])


def extract_isolated(path,options,timeout=90,cancel_event=None):
    # A bad PDF cannot hang the whole refresh indefinitely.
    process=subprocess.Popen([sys.executable,'-m','local_search.pdf_worker',str(path),json.dumps(options)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    started=time.monotonic()
    try:
        while True:
            check_cancel(cancel_event)
            if time.monotonic()-started>=timeout:raise TimeoutError('PDF extraction exceeded '+str(timeout)+' seconds')
            try:stdout,stderr=process.communicate(timeout=.1);break
            except subprocess.TimeoutExpired:pass
        if process.returncode:raise ValueError('PDF worker failed: '+stderr.decode('utf-8',errors='replace')[-500:])
        return json.loads(stdout)
    finally:
        if process.poll() is None:process.kill();process.communicate()


def ingest_folder(library,config,extractor=extract_isolated,on_progress=None,on_failure=None,inventory=None,cancel_event=None):
    if inventory is None:
        manifest=Inventory(library.path.parent)
        progress=Progress(config['name'],{'indexed':0},on_progress)
        progress.emit('Starting',config['path'],force=True)
        try:
            discover_folder(config,manifest,lambda path,n:progress.emit('Finding candidates: '+str(n)+' found',path),on_failure,cancel_event)
            return ingest_folder(library,config,extractor,on_progress,on_failure,manifest,cancel_event)
        except RefreshCancelled:
            return {'collection':config['name'],'cancelled':True,'processed':0,'deletion_reconciliation':'skipped because refresh was cancelled'}
        finally:manifest.close()
    root=Path(config['path']).expanduser().resolve();collection=config['name']
    if not root.is_dir():raise ValueError(f'Folder unavailable; retained index: {root}')
    options={k:config[k] for k in ('min_words','ascii_ratio','max_pages') if k in config}
    try:pdf_version=importlib.metadata.version('pypdf')
    except importlib.metadata.PackageNotFoundError:pdf_version='unavailable'
    policy=sha(json.dumps([EXTRACT_VERSION,pdf_version,options],sort_keys=True))
    seen=set();scan_errors=inventory.scan_errors;stats=dict(collection=collection,hashed=0,extracted=0,cached=0,indexed=0,skipped=0,errors=0,removed=0)
    progress=Progress(collection,stats,on_progress,total=inventory.count);progress.emit('Starting',root,force=True)
    def failure(path,reason,kind='ERROR'):
        if on_failure:on_failure(collection,path,reason,kind)
    try:
        for candidate in inventory:
            path=Path(candidate)
            check_cancel(cancel_event)
            key=path.relative_to(root).as_posix();seen.add(key)
            progress.emit('Checking file',path,force=True)
            created=None;modified=None;completed=False
            try:
                stat=path.stat()
                modified=date(stat.st_mtime)
                created=date(getattr(stat,'st_birthtime',stat.st_ctime if os.name=='nt' else None))
                if stat.st_size>config.get('max_file_mb',200)*1024*1024:
                    with library.db:library.put(collection,key,path.stem,str(path),[],modified_at=modified,added_at=created,added_basis='filesystem_creation' if created else None,status='skipped',detail={'reason':'file size limit exceeded'})
                    stats['skipped']+=1;completed=True;failure(path,'file size limit exceeded','SKIPPED');continue
                progress.emit('Hashing',path,force=True)
                h=file_hash(path,cancel_event);stats['hashed']+=1
                cached=library.db.execute('SELECT result FROM extraction_cache WHERE byte_hash=? AND policy=?',(h,policy)).fetchone()
                if cached:result=json.loads(cached[0]);stats['cached']+=1
                else:
                    progress.emit('Extracting PDF (up to 90s timeout)',path,force=True)
                    result=extractor(path,options,cancel_event=cancel_event) if extractor is extract_isolated else extractor(path,options);stats['extracted']+=1
                    if file_hash(path,cancel_event)!=h:raise ValueError('File changed during extraction; retry next refresh')
                if path.stat().st_mtime_ns!=stat.st_mtime_ns:raise ValueError('File changed during scan')
                status='indexed' if result['segments'] else 'skipped'
                progress.emit('Indexing cached text' if cached else 'Indexing extracted text',path,force=True)
                with library.db:
                    if not cached:library.db.execute('INSERT OR REPLACE INTO extraction_cache VALUES(?,?,?)',(h,policy,json.dumps(result,ensure_ascii=False)))
                    library.put(collection,key,result.get('title') or path.stem,str(path),result['segments'],byte_hash=h,
                        added_at=created,added_basis='filesystem_creation' if created else None,modified_at=modified,status=status,
                        detail={k:v for k,v in result.items() if k not in ('segments','title')})
                stats[status]+=1
                completed=True
                if status=='skipped':failure(path,result.get('reason') or 'no accepted text','SKIPPED')
                elif any(not p.get('accepted',True) for p in result.get('quality',[])):
                    rejected=[p.get('page') for p in result['quality'] if not p.get('accepted',True)]
                    failure(path,'Rejected PDF pages: '+str(rejected),'PARTIAL')
            except RefreshCancelled:raise
            except Exception as error:
                stats['errors']+=1
                failure(path,error)
                # Do not serve stale content after a known failed update.
                with library.db:library.put(collection,key,path.stem,str(path),[],status='error',added_at=created,added_basis='filesystem_creation' if created else None,modified_at=modified,detail={'reason':str(error)[:1000]})
                completed=True
            finally:
                if completed:progress.processed+=1
                progress.emit('File processed',path)
    except RefreshCancelled:
        stats.update(cancelled=True,processed=progress.processed,deletion_reconciliation='skipped because refresh was cancelled')
        progress.emit('Cancelled',root,force=True);return stats
    stats['processed']=progress.processed
    progress.emit('Reconciling deletions / cleaning index',root,force=True)
    if cancel_event is not None and cancel_event.is_set():
        stats.update(cancelled=True,deletion_reconciliation='skipped because refresh was cancelled')
        progress.emit('Cancelled',root,force=True);return stats
    if not scan_errors and not (cancel_event is not None and cancel_event.is_set()):
        with library.db:stats['removed']=library.prune(collection,seen)
    else:stats['scan_errors']=scan_errors;stats['deletion_reconciliation']='skipped because traversal was incomplete'
    progress.total=progress.processed;progress.emit('Complete',root,force=True)
    return stats


class HTMLText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if tag in ('p','div','br','li','h1','h2','h3'):self.parts.append('\n\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,text):
        if not self.hidden:self.parts.append(text)


@contextmanager
def clipper_connection(path,closed_snapshot=False,scratch=None,network_mode=None,cancel_event=None):
    path=Path(path).resolve()
    check_cancel(cancel_event)
    if not path.is_file():raise ValueError('Clipper database is unavailable; check share access and permissions')
    remote=network_path(path)
    if network_mode not in (None,'closed_snapshot','export'):raise ValueError('Network clipper mode must be closed_snapshot or export')
    if remote:
        if not closed_snapshot and network_mode is None:
            raise ValueError('Network clipper needs a closed snapshot or completed export. Select Closed snapshot after stopping/checkpointing the clipper, or set network_mode to export for an atomically published completed export. Live remote WAL is unsupported.')
        closed_snapshot=True
    if network_mode is not None:closed_snapshot=True
    if not closed_snapshot:
        db=connect_readonly(path)
        try:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN');yield db
        finally:db.close()
    else:
        # Explicit opt-in for CLOSED databases on read-only media. Never ignore a live WAL.
        sidecars=[Path(str(path)+suffix) for suffix in ('-wal','-journal')]
        if any(p.exists() for p in sidecars):raise ValueError('Close/checkpoint the clipper first; snapshot refuses WAL/journal sidecars')
        before=path.stat()
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            copy=Path(tmp)/'source.db'
            with path.open('rb') as src,copy.open('wb') as dst:
                while True:
                    check_cancel(cancel_event);block=src.read(1024*1024)
                    if not block:break
                    dst.write(block)
            if any(p.exists() for p in sidecars) or path.stat().st_mtime_ns!=before.st_mtime_ns or file_hash(path,cancel_event)!=file_hash(copy,cancel_event):
                raise ValueError('Clipper changed while making closed snapshot')
            db=connect_readonly(copy,immutable=True)
            try:db.execute('PRAGMA query_only=ON');yield db
            finally:db.close()


def discover_clipper(library,config,inventory,on_progress=None,cancel_event=None):
    path=Path(config['path']).expanduser().resolve()
    with clipper_connection(path,config.get('closed_snapshot',False),library.path.parent,config.get('network_mode'),cancel_event) as source:
        source.row_factory=sqlite3.Row
        columns={r[1] for r in source.execute('PRAGMA table_info(clips)')}
        if not {'id','title','captured_at'}<=columns or not {'content_text','content_html'}&columns:
            raise ValueError('Unsupported clipper schema; expected clips(id,title,captured_at,content_text/content_html)')
        wanted=['id','title','captured_at','url','canonical_url','content_text','content_html','date_published_norm','date_precision']
        selection=','.join('"'+c+'"' if c in columns else 'NULL AS "'+c+'"' for c in wanted)
        for row in source.execute('SELECT '+selection+' FROM clips ORDER BY id'):
            check_cancel(cancel_event);inventory.add(dict(row))
            if on_progress:on_progress(path,inventory.count)
    # Remote transaction is closed before any PDF extraction or index processing.


def ingest_clipper(library,config,on_progress=None,on_failure=None,inventory=None,cancel_event=None):
    if inventory is None:
        manifest=Inventory(library.path.parent)
        try:
            discover_clipper(library,config,manifest,cancel_event=cancel_event)
            return ingest_clipper(library,config,on_progress,on_failure,manifest,cancel_event)
        except RefreshCancelled:
            return {'collection':config['name'],'cancelled':True,'processed':0,'retained_existing_index':True}
        finally:manifest.close()
    path=Path(config['path']).expanduser().resolve();collection=config['name'];seen=set()
    stats=dict(collection=collection,indexed=0,skipped=0,removed=0,processed=0)
    progress=Progress(collection,stats,on_progress,total=inventory.count);progress.emit('Starting',path,force=True)
    try:
        with library.db:
            for row in inventory:
                check_cancel(cancel_event)
                key=str(row['id']);seen.add(key)
                progress.emit('Processing clip (commit pending)',str(path)+'#clip='+key,force=True)
                text=row['content_text'] or ''
                if not text.strip() and row['content_html']:
                    parser=HTMLText();parser.feed(row['content_html']);text=''.join(parser.parts).strip()
                status='indexed' if text.strip() else 'skipped'
                library.put(collection,key,row['title'] or '',row['canonical_url'] or row['url'] or str(path)+'#clip='+key,
                    [{'text':text,'page':None}] if text else [],byte_hash=sha(text),
                    added_at=date(row['captured_at']),added_basis='clipper_captured_at',
                    published_at=date(row['date_published_norm']),date_precision=row['date_precision'],status=status,
                    detail={'source_database':str(path),'clip_id':row['id'],'reason':'no stored text; PDF attachments are not implicitly extracted' if not text else None})
                stats[status]+=1
                if status=='skipped' and on_failure:on_failure(collection,str(path)+'#clip='+key,'no stored text; PDF attachments are not implicitly extracted','SKIPPED')
                progress.processed+=1;progress.emit('Clip processed (commit pending)')
            check_cancel(cancel_event)
            progress.emit('Reconciling deletions / committing',path,force=True)
            check_cancel(cancel_event)
            stats['removed']=library.prune(collection,seen)
    except RefreshCancelled:
        stats.update(indexed=0,skipped=0,removed=0,cancelled=True,rolled_back=True,retained_existing_index=True)
    stats['processed']=progress.processed
    progress.emit('Cancelled' if stats.get('cancelled') else 'Complete',path,force=True)
    return stats


def refresh(library,config,on_progress=None,cancel_event=None):
    reports=[];manifests=[];started=time.monotonic();found=0;processed=0;frozen_total=None
    log=FailureLog(library.path.parent/'ingestion.log')
    def emit(phase,current=None,index=0,total=None,stats=None):
        if stats is None:stats={key:sum(r.get(key,0) for r in reports) for key in ('indexed','cached','extracted','skipped','errors')}
        if on_progress:on_progress({'type':'ingest_progress','collection':'library','phase':phase,'current':str(current or ''),
            'processed':processed,'total':total,'found':found,'stats':stats or {},'elapsed_seconds':time.monotonic()-started,
            'source_number':index,'source_collection':config['sources'][index-1]['name'] if index else '',
            'source_count':len(config['sources'])})
    try:
        with ExitStack() as stack:
            emit('Starting')
            for index,source in enumerate(config['sources'],1):
                check_cancel(cancel_event)
                manifest=Inventory(library.path.parent);stack.callback(manifest.close)
                def discovered(path,count):
                    nonlocal found
                    found=sum(m.count for _,_,m in manifests)+count
                    emit('Finding candidates: '+str(found)+' found',path,index)
                try:
                    if source['type']=='folder':discover_folder(source,manifest,discovered,log.failure,cancel_event)
                    else:discover_clipper(library,source,manifest,discovered,cancel_event)
                    check_cancel(cancel_event)
                    manifests.append((index,source,manifest));found=sum(m.count for _,_,m in manifests)
                except RefreshCancelled:raise
                except Exception as error:
                    log.failure(source['name'],source['path'],error)
                    reports.append({'collection':source['name'],'error':str(error),'retained_existing_index':True,'log_file':str(log.path)})
                    emit('Source failed',source['path'],index)
            total=sum(m.count for _,_,m in manifests)
            frozen_total=total
            emit('Processing candidates',total=total)
            for index,source,manifest in manifests:
                check_cancel(cancel_event);base=processed;last=0;latest_stats={}
                def progress(event):
                    nonlocal processed,last,latest_stats
                    last=max(last,event['processed']);processed=base+last
                    latest_stats=dict(event['stats'])
                    combined={key:sum(r.get(key,0) for r in reports)+event['stats'].get(key,0) for key in ('indexed','cached','extracted','skipped','errors')}
                    if on_progress:on_progress(dict(event,collection='library',source_collection=source['name'],source_number=index,
                        source_count=len(config['sources']),processed=processed,total=total,found=found,stats=combined,elapsed_seconds=time.monotonic()-started))
                try:
                    report=(ingest_folder if source['type']=='folder' else ingest_clipper)(library,source,on_progress=progress,on_failure=log.failure,inventory=manifest,cancel_event=cancel_event)
                    processed=base+report.get('processed',last)
                    report['log_file']=str(log.path);reports.append(report)
                    if report.get('cancelled'):raise RefreshCancelled()
                except RefreshCancelled:raise
                except Exception as error:
                    log.failure(source['name'],source['path'],error)
                    # Remaining frozen candidates have a failed source outcome; do not shrink the denominator.
                    processed=base+manifest.count
                    outcomes=latest_stats if source['type']=='folder' else {'indexed':0,'skipped':0,'errors':last}
                    reports.append({**outcomes,'collection':source['name'],'error':str(error),'retained_existing_index':source['type']=='clipper' or last==0,
                                    'processed':manifest.count,'errors':outcomes.get('errors',0)+manifest.count-last,'log_file':str(log.path)})
                    emit('Source failed',source['path'],index,total)
            emit('Finalising / reconciling',total=total)
            errors=any(r.get('error') or r.get('errors') or r.get('scan_errors') for r in reports)
            emit('Complete with errors' if errors else 'Complete',total=total,
                 stats={key:sum(r.get(key,0) for r in reports) for key in ('indexed','cached','extracted','skipped','errors')})
    except RefreshCancelled:
        if not reports or not reports[-1].get('cancelled'):reports.append({'collection':'library','cancelled':True,'processed':0,'deletion_reconciliation':'skipped for unprocessed sources'})
        emit('Cancelled',total=frozen_total)
    finally:log.close()
    order={s['name']:i for i,s in enumerate(config['sources'])}
    return sorted(reports,key=lambda r:order.get(r['collection'],len(order)))
