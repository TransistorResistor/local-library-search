"""Review operations with optimistic concurrency and short, guarded transactions."""
import hashlib
import json
import sqlite3
import time
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


def _fetch(db,ident):
    row=db.execute('SELECT * FROM metadata_jobs WHERE id=?',(ident,)).fetchone()
    if row is None:raise ValueError('This finding no longer exists; refresh the list')
    job=dict(row);profile=db.execute('SELECT definition,prompt FROM metadata_profiles WHERE hash=?',(job['profile_hash'],)).fetchone()
    overrides={r['field']:json.loads(r['value']) for r in db.execute('SELECT field,value FROM metadata_overrides WHERE source_uuid=?',(job['source_uuid'],))}
    token=hashlib.sha256(canonical({'job':job,'overrides':overrides}).encode()).hexdigest()
    current=current_source(db,job['source_id'])
    source=db.execute('SELECT title,locator FROM sources WHERE source_uuid=?',(job['source_uuid'],)).fetchone()
    latest=db.execute('SELECT result FROM metadata_runs WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(ident,)).fetchone()
    manual_fields=json.loads(latest[0]).get('review_manual_fields',[]) if latest else []
    return {'job':job,'profile':validate_profile(json.loads(profile[0])),'prompt':profile[1],'metadata':json.loads(job['result']) if job['result'] else None,
            'excerpts':json.loads(job['evidence'] or '[]'),'issues':json.loads(job['issues']),'overrides':overrides,'review_token':token,
            'manual_fields':manual_fields,'title':source[0] if source else 'Removed source','locator':source[1] if source else None,
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
            commit_result(SimpleNamespace(db=db),job,result,profile)
            if accept:
                for name in manual_fields:
                    value=canonical(parsed[name]);now=time.time()
                    db.execute('INSERT INTO metadata_overrides VALUES(?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(job['source_uuid'],name,value,now))
                    db.execute('INSERT INTO metadata_values VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET content_hash=excluded.content_hash,profile_hash=excluded.profile_hash,value=excluded.value,status=excluded.status,origin=excluded.origin,job_id=excluded.job_id,updated_at=excluded.updated_at',
                               (job['source_uuid'],name,job['content_hash'],job['profile_hash'],value,'accepted','manual',ident,now))
            db.commit()
        except BaseException:db.rollback();raise
    return fetch(path,ident)
