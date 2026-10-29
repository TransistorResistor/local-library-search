"""Review operations with optimistic concurrency and short, guarded transactions."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from .library_guard import LibraryGuard
from .sqlite_paths import connect_readonly,readonly_uri
from .metadata_profile import canonical,validate_profile,parse_and_validate
from .metadata_store import inspect,current_source,commit_result


@contextmanager
def connection(path,write=False):
    with LibraryGuard(path):
        db=sqlite3.connect(readonly_uri(path).replace('?mode=ro','?mode=rw'),uri=True,timeout=3) if write else connect_readonly(path)
        db.row_factory=sqlite3.Row
        try:
            if inspect(db)['metadata_schema'] is None:raise ValueError('Apply the metadata migration before reviewing saved findings')
            db.execute('PRAGMA foreign_keys=ON');yield db
        finally:db.close()


def jobs(path,status='needs_review'):
    with connection(path) as db:
        return [dict(r) for r in db.execute('''SELECT j.id,j.source_id,j.status,j.updated_at,s.title,s.collection
          FROM metadata_jobs j LEFT JOIN sources s ON s.source_uuid=j.source_uuid
          WHERE (?='all' OR j.status=?) ORDER BY j.created_at,j.rowid''',(status,status))]


def queue_page(path,status='needs_review',query='',collection='',profile='',offset=0,limit=50):
    offset=max(0,int(offset));limit=max(1,min(200,int(limit)))
    where="""WHERE (?='all' OR j.status=?) AND (?='' OR instr(lower(coalesce(s.title,'')),lower(?))>0)
      AND (?='' OR s.collection=?) AND (?='' OR instr(lower(p.name),lower(?))>0)"""
    args=(status,status,query,query,collection,collection,profile,profile)
    base='FROM metadata_jobs j LEFT JOIN sources s ON s.source_uuid=j.source_uuid JOIN metadata_profiles p ON p.hash=j.profile_hash '
    with connection(path) as db:
        db.execute('BEGIN')
        total=db.execute('SELECT count(*) '+base+where,args).fetchone()[0]
        items=[dict(r) for r in db.execute('SELECT j.id,j.status,s.title,s.collection,p.name AS profile '+base+where+' ORDER BY j.created_at,j.rowid LIMIT ? OFFSET ?',(*args,limit,offset))]
        counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM metadata_jobs GROUP BY status')}
        collections=[r[0] for r in db.execute('SELECT DISTINCT collection FROM sources ORDER BY collection')]
        return {'items':items,'total':total,'offset':offset,'limit':limit,'counts':counts,'collections':collections,'profiles':[r[0] for r in db.execute('SELECT DISTINCT name FROM metadata_profiles ORDER BY name')]}


def _fetch(db,ident):
    row=db.execute('SELECT * FROM metadata_jobs WHERE id=?',(ident,)).fetchone()
    if row is None:raise ValueError('This finding no longer exists; refresh the list')
    job=dict(row);profile=db.execute('SELECT definition,prompt FROM metadata_profiles WHERE hash=?',(job['profile_hash'],)).fetchone()
    overrides={r['field']:json.loads(r['value']) for r in db.execute('SELECT field,value FROM metadata_overrides WHERE source_uuid=?',(job['source_uuid'],))}
    values={r['field']:{**dict(r),'value':json.loads(r['value'])} for r in db.execute('SELECT * FROM metadata_values WHERE source_uuid=?',(job['source_uuid'],))}
    token=hashlib.sha256(canonical({'job':job,'overrides':overrides,'current_values':values}).encode()).hexdigest()
    current=current_source(db,job['source_id'])
    source=db.execute('SELECT title,locator FROM sources WHERE source_uuid=?',(job['source_uuid'],)).fetchone()
    latest=db.execute('SELECT result FROM metadata_runs WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(ident,)).fetchone()
    manual_fields=json.loads(latest[0]).get('review_manual_fields',[]) if latest else []
    return {'job':job,'profile':validate_profile(json.loads(profile[0])),'prompt':profile[1],'metadata':json.loads(job['result']) if job['result'] else None,
            'excerpts':json.loads(job['evidence'] or '[]'),'issues':json.loads(job['issues']),'overrides':overrides,'review_token':token,
            'manual_fields':manual_fields,'current_values':values,'title':source[0] if source else 'Removed source','locator':source[1] if source else None,
            'stale':not current or current['source_uuid']!=job['source_uuid'] or current['text_hash']!=job['content_hash']}


def fetch(path,ident):
    with connection(path) as db:
        db.execute('BEGIN');return _fetch(db,ident)


def save(path,ident,metadata,token,manual_fields=(),accept=False):
    with connection(path,True) as db:
        try:
            db.execute('BEGIN IMMEDIATE');record=_fetch(db,ident);job=record['job']
            if record['review_token']!=token:raise ValueError('This finding was changed elsewhere. Reload it before saving.')
            if record['stale'] or job['status']=='stale':raise ValueError('The source changed or was removed; extract a fresh finding before accepting it.')
            profile=record['profile'];names={f['name'] for f in profile['fields']};manual_fields=set(manual_fields)|set(record['manual_fields'])
            if manual_fields-names:raise ValueError('Unknown edited fields')
            source=db.execute('SELECT doc_id FROM sources WHERE source_uuid=?',(job['source_uuid'],)).fetchone()
            for excerpt in record['excerpts']:
                actual=db.execute('SELECT substr(text,?,?) FROM documents WHERE id=?',(excerpt['start_char']+1,len(excerpt['text']),source[0])).fetchone()[0]
                if actual!=excerpt['text']:raise ValueError('Stored evidence no longer matches the indexed document')
            checked={**profile,'fields':[dict(f,evidence=False) if f['name'] in manual_fields else f for f in profile['fields']]}
            parsed,issues=parse_and_validate(canonical(metadata),checked,record['excerpts'])
            if accept and issues:raise ValueError('Fix the following before accepting: '+'; '.join(issues))
            result={'status':'accepted' if accept else 'needs_review','metadata':parsed,'issues':issues,'excerpts':record['excerpts'],
                    'attempts':json.loads(job['attempts']),'review_manual_fields':sorted(manual_fields),'review_action':'accept' if accept else 'save_draft'}
            if accept:
                commit_result(SimpleNamespace(db=db),job,result,profile)
                for name in names:
                    if name not in manual_fields and (parsed[name] is not None or name in record['overrides']):continue
                    value=canonical(parsed[name]);now=time.time()
                    if name in manual_fields:db.execute('INSERT INTO metadata_overrides VALUES(?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(job['source_uuid'],name,value,now))
                    db.execute('INSERT INTO metadata_values VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET content_hash=excluded.content_hash,profile_hash=excluded.profile_hash,value=excluded.value,status=excluded.status,origin=excluded.origin,job_id=excluded.job_id,updated_at=excluded.updated_at',
                               (job['source_uuid'],name,job['content_hash'],job['profile_hash'],value,'accepted','manual' if name in manual_fields else 'extracted',ident,now))
            else:
                # A review draft is a proposal. It must never publish field values.
                db.execute("UPDATE metadata_jobs SET status='needs_review',result=?,issues=?,updated_at=? WHERE id=?",(canonical(parsed),canonical(issues),time.time(),ident))
                db.execute('INSERT INTO metadata_runs VALUES(?,?,?,?,?)',(str(uuid.uuid4()),ident,job.get('model_hash'),canonical(result),time.time()))
            db.commit()
        except BaseException:db.rollback();raise
    return fetch(path,ident)
