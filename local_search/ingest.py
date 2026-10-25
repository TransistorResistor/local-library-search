"""Read-only clipper and recursive PDF ingestion; write only the owned library."""
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from .store import sha, utc_now

EXTRACT_VERSION='pdf-text-v1'


def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
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


def extract_isolated(path,options,timeout=90):
    # A bad PDF cannot hang the whole refresh indefinitely.
    process=subprocess.run([sys.executable,'-m','local_search.pdf_worker',str(path),json.dumps(options)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False,
        cwd=str(Path(__file__).resolve().parent.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if process.returncode:raise ValueError('PDF worker failed: '+process.stderr.decode('utf-8',errors='replace')[-500:])
    return json.loads(process.stdout)


def ingest_folder(library,config,extractor=extract_isolated):
    root=Path(config['path']).expanduser().resolve();collection=config['name']
    if not root.is_dir():raise ValueError(f'Folder unavailable; retained index: {root}')
    options={k:config[k] for k in ('min_words','ascii_ratio','max_pages') if k in config}
    try:pdf_version=importlib.metadata.version('pypdf')
    except importlib.metadata.PackageNotFoundError:pdf_version='unavailable'
    policy=sha(json.dumps([EXTRACT_VERSION,pdf_version,options],sort_keys=True))
    seen=set();scan_errors=[];stats=dict(collection=collection,hashed=0,extracted=0,cached=0,indexed=0,skipped=0,errors=0,removed=0)
    def onerror(error):scan_errors.append(str(error))
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=onerror):
        dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink() and not (hasattr(Path(folder)/d,'is_junction') and (Path(folder)/d).is_junction())]
        for name in sorted(files):
            path=Path(folder)/name
            if path.suffix.lower()!='.pdf' or path.is_symlink():continue
            key=path.relative_to(root).as_posix();seen.add(key)
            created=None;modified=None
            try:
                stat=path.stat()
                modified=date(stat.st_mtime)
                created=date(getattr(stat,'st_birthtime',stat.st_ctime if os.name=='nt' else None))
                if stat.st_size>config.get('max_file_mb',200)*1024*1024:
                    with library.db:library.put(collection,key,path.stem,str(path),[],modified_at=modified,added_at=created,added_basis='filesystem_creation' if created else None,status='skipped',detail={'reason':'file size limit exceeded'})
                    stats['skipped']+=1;continue
                h=file_hash(path);stats['hashed']+=1
                cached=library.db.execute('SELECT result FROM extraction_cache WHERE byte_hash=? AND policy=?',(h,policy)).fetchone()
                if cached:result=json.loads(cached[0]);stats['cached']+=1
                else:
                    result=extractor(path,options);stats['extracted']+=1
                    if file_hash(path)!=h:raise ValueError('File changed during extraction; retry next refresh')
                if path.stat().st_mtime_ns!=stat.st_mtime_ns:raise ValueError('File changed during scan')
                status='indexed' if result['segments'] else 'skipped'
                with library.db:
                    if not cached:library.db.execute('INSERT OR REPLACE INTO extraction_cache VALUES(?,?,?)',(h,policy,json.dumps(result,ensure_ascii=False)))
                    library.put(collection,key,result.get('title') or path.stem,str(path),result['segments'],byte_hash=h,
                        added_at=created,added_basis='filesystem_creation' if created else None,modified_at=modified,status=status,
                        detail={k:v for k,v in result.items() if k not in ('segments','title')})
                stats[status]+=1
            except Exception as error:
                stats['errors']+=1
                # Do not serve stale content after a known failed update.
                with library.db:library.put(collection,key,path.stem,str(path),[],status='error',added_at=created,added_basis='filesystem_creation' if created else None,modified_at=modified,detail={'reason':str(error)[:1000]})
    if not scan_errors:
        with library.db:stats['removed']=library.prune(collection,seen)
    else:stats['scan_errors']=scan_errors;stats['deletion_reconciliation']='skipped because traversal was incomplete'
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
def clipper_connection(path,closed_snapshot=False,scratch=None):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('Clipper database is unavailable')
    if not closed_snapshot:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
        try:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN');yield db
        finally:db.close()
    else:
        # Explicit opt-in for CLOSED databases on read-only media. Never ignore a live WAL.
        sidecars=[Path(str(path)+suffix) for suffix in ('-wal','-journal')]
        if any(p.exists() for p in sidecars):raise ValueError('Close/checkpoint the clipper first; snapshot refuses WAL/journal sidecars')
        before=path.stat()
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            copy=Path(tmp)/'source.db';shutil.copyfile(path,copy)
            if any(p.exists() for p in sidecars) or path.stat().st_mtime_ns!=before.st_mtime_ns or file_hash(path)!=file_hash(copy):
                raise ValueError('Clipper changed while making closed snapshot')
            db=sqlite3.connect(copy.as_uri()+'?mode=ro&immutable=1',uri=True)
            try:db.execute('PRAGMA query_only=ON');yield db
            finally:db.close()


def ingest_clipper(library,config):
    path=Path(config['path']).expanduser().resolve();collection=config['name'];seen=set()
    stats=dict(collection=collection,indexed=0,skipped=0,removed=0)
    with clipper_connection(path,config.get('closed_snapshot',False),library.path.parent) as source:
        source.row_factory=sqlite3.Row
        columns={r[1] for r in source.execute('PRAGMA table_info(clips)')}
        required={'id','title','captured_at'}
        if not required<=columns or not {'content_text','content_html'}&columns:raise ValueError('Unsupported clipper schema; expected clips(id,title,captured_at,content_text/content_html)')
        wanted=['id','title','captured_at','url','canonical_url','content_text','content_html','date_published_norm','date_precision']
        selection=','.join('"'+c+'"' if c in columns else 'NULL AS "'+c+'"' for c in wanted)
        with library.db:
            for row in source.execute('SELECT '+selection+' FROM clips ORDER BY id'):
                key=str(row['id']);seen.add(key)
                text=row['content_text'] or ''
                if not text.strip() and row['content_html']:
                    parser=HTMLText();parser.feed(row['content_html']);text=''.join(parser.parts).strip()
                status='indexed' if text.strip() else 'skipped'
                library.put(collection,key,row['title'],row['canonical_url'] or row['url'] or str(path)+'#clip='+key,
                    [{'text':text,'page':None}] if text else [],byte_hash=sha(text),
                    added_at=date(row['captured_at']),added_basis='clipper_captured_at',
                    published_at=date(row['date_published_norm']),date_precision=row['date_precision'],status=status,
                    detail={'source_database':str(path),'clip_id':row['id'],'reason':'no stored text; PDF attachments are not implicitly extracted' if not text else None})
                stats[status]+=1
            stats['removed']=library.prune(collection,seen)
    return stats


def refresh(library,config):
    reports=[]
    for source in config['sources']:
        try:reports.append((ingest_folder if source['type']=='folder' else ingest_clipper)(library,source))
        except Exception as error:reports.append({'collection':source['name'],'error':str(error),'retained_existing_index':True})
    return reports
