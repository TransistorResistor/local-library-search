"""Additive metadata migration, durable jobs and source-specific manual overrides."""
import sqlite3
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from .metadata_profile import canonical,profile_hash,validate_profile


TABLES=[
 'CREATE TABLE metadata_info(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
 'CREATE TABLE metadata_profiles(hash TEXT PRIMARY KEY,name TEXT NOT NULL,definition TEXT NOT NULL,prompt TEXT NOT NULL,created_at REAL NOT NULL)',
 'CREATE TABLE metadata_policies(collection TEXT PRIMARY KEY,profile_hash TEXT NOT NULL REFERENCES metadata_profiles(hash),mode TEXT NOT NULL CHECK(mode IN (\'missing\',\'changed\')))',
 '''CREATE TABLE metadata_jobs(id TEXT PRIMARY KEY,source_uuid TEXT NOT NULL,source_id INTEGER NOT NULL,content_hash TEXT NOT NULL,
 profile_hash TEXT NOT NULL REFERENCES metadata_profiles(hash),model_hash TEXT,status TEXT NOT NULL,attempts TEXT NOT NULL DEFAULT '[]',result TEXT,evidence TEXT,
 issues TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL,updated_at REAL NOT NULL,UNIQUE(source_uuid,content_hash,profile_hash))''',
 '''CREATE TABLE metadata_values(source_uuid TEXT NOT NULL,field TEXT NOT NULL,content_hash TEXT NOT NULL,profile_hash TEXT NOT NULL,
 value TEXT NOT NULL,status TEXT NOT NULL,origin TEXT NOT NULL,job_id TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(source_uuid,field))''',
 'CREATE TABLE metadata_overrides(source_uuid TEXT NOT NULL,field TEXT NOT NULL,value TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(source_uuid,field))',
 'CREATE TABLE metadata_runs(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES metadata_jobs(id),model_hash TEXT,result TEXT NOT NULL,created_at REAL NOT NULL)',
 'CREATE INDEX metadata_jobs_status ON metadata_jobs(status,created_at)'
]


def migrated(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata_info'").fetchone() is not None


def inspect(db):
    row=db.execute("SELECT value FROM settings WHERE key='schema'").fetchone()
    if not row or row[0] not in ('1','2'):raise ValueError('Not a supported search library')
    result={'core_schema':row[0],'metadata_schema':None,'eligible_sources':db.execute("SELECT count(*) FROM sources WHERE status='indexed' AND doc_id IS NOT NULL").fetchone()[0]}
    if migrated(db):
        version=db.execute("SELECT value FROM metadata_info WHERE key='schema'").fetchone()
        if not version or version[0]!='1':raise ValueError('Unsupported metadata schema')
        result['metadata_schema']=version[0]
        result['jobs']=[dict(zip(('status','count'),r)) for r in db.execute('SELECT status,count(*) FROM metadata_jobs GROUP BY status')]
    return result


def migrate(library,apply=False):
    preview=_migrate(library,False)
    if not apply or preview['metadata_schema']:return preview
    from .library_guard import maintenance
    with maintenance(library):return _migrate_exclusive(library)


def _migrate_exclusive(library):
    db=library.db
    if db.in_transaction:raise ValueError('Finish the current transaction before metadata migration')
    # Retain an exclusive SQLite lock across backup and DDL, without backing up
    # from an active write transaction (which would block the backup API).
    busy_timeout=db.execute('PRAGMA busy_timeout').fetchone()[0]
    db.execute('PRAGMA busy_timeout=1000');db.execute('PRAGMA locking_mode=EXCLUSIVE')
    try:
        db.execute('BEGIN EXCLUSIVE');db.commit()
        return _migrate(library,True)
    except sqlite3.OperationalError as error:
        if 'locked' in str(error).lower():raise ValueError('Close the app and other library readers/workers before applying the metadata migration') from error
        raise
    finally:
        if db.in_transaction:db.rollback()
        db.execute('PRAGMA locking_mode=NORMAL')
        db.execute('BEGIN');db.execute('SELECT value FROM settings LIMIT 1').fetchall();db.commit()
        db.execute(f'PRAGMA busy_timeout={busy_timeout}')


def _migrate(library,apply=False):
    db=library.db;before=inspect(db)
    preview={**before,'action':'no-op' if before['metadata_schema'] else 'add metadata tables, source UUIDs and revision tracking; core schema 2','applied':False}
    if before['metadata_schema'] or not apply:return preview
    if db.in_transaction:raise ValueError('Finish the current transaction before metadata migration')
    backup_path=library.path.parent/'before-metadata.db'
    if backup_path.resolve()==library.path.resolve():raise ValueError('Backup must differ from the library')
    # Never overwrite the first pre-migration recovery point.
    if backup_path.exists():backup_path=library.path.parent/('before-metadata-'+uuid.uuid4().hex+'.db')
    backup=sqlite3.connect(backup_path)
    try:db.backup(backup)
    finally:backup.close()
    check=sqlite3.connect(backup_path)
    try:
        if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Pre-migration backup validation failed')
    finally:check.close()
    try:
        db.execute('BEGIN IMMEDIATE')
        # A schema marker and management row also make older binaries reject writes.
        db.execute('CREATE TABLE IF NOT EXISTS library_management(id INTEGER PRIMARY KEY CHECK(id=1),uuid TEXT NOT NULL,revision INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO library_management VALUES(1,?,0)',(str(uuid.uuid4()),))
        columns={r[1] for r in db.execute('PRAGMA table_info(sources)')}
        if 'source_uuid' not in columns:db.execute('ALTER TABLE sources ADD COLUMN source_uuid TEXT')
        db.execute('UPDATE sources SET source_uuid=lower(hex(randomblob(16))) WHERE source_uuid IS NULL')
        db.execute('CREATE UNIQUE INDEX metadata_source_uuid ON sources(source_uuid)')
        for statement in TABLES:db.execute(statement)
        db.execute("INSERT INTO metadata_info VALUES('schema','1')")
        db.execute("INSERT INTO metadata_info VALUES('migration_id',?)",(str(uuid.uuid4()),))
        db.execute('''CREATE TRIGGER metadata_source_identity AFTER INSERT ON sources WHEN new.source_uuid IS NULL
          BEGIN UPDATE sources SET source_uuid=lower(hex(randomblob(16))) WHERE id=new.id; END''')
        db.execute('''CREATE TRIGGER metadata_source_relocated AFTER UPDATE OF locator ON sources WHEN old.locator IS NOT new.locator
          BEGIN UPDATE sources SET source_uuid=lower(hex(randomblob(16))) WHERE id=new.id;
          UPDATE metadata_jobs SET status='stale',updated_at=strftime('%s','now') WHERE source_uuid=old.source_uuid AND status IN ('pending','running'); END''')
        db.execute('''CREATE TRIGGER metadata_source_deleted AFTER DELETE ON sources BEGIN
          UPDATE metadata_jobs SET status='stale',updated_at=strftime('%s','now') WHERE source_uuid=old.source_uuid; END''')
        # Database-level outbox also covers a connection opened before migration.
        for event in ('INSERT','UPDATE'):
            db.execute(f'''CREATE TRIGGER metadata_queue_{event.lower()} AFTER {event} ON sources BEGIN
              UPDATE metadata_jobs SET status='stale',updated_at=strftime('%s','now')
                WHERE source_uuid=(SELECT source_uuid FROM sources WHERE id=new.id)
                AND content_hash IS NOT (SELECT d.text_hash FROM sources s LEFT JOIN documents d ON d.id=s.doc_id WHERE s.id=new.id)
                AND status!='stale';
              INSERT OR IGNORE INTO metadata_jobs(id,source_uuid,source_id,content_hash,profile_hash,status,created_at,updated_at)
                SELECT lower(hex(randomblob(16))),s.source_uuid,s.id,d.text_hash,p.profile_hash,'pending',strftime('%s','now'),strftime('%s','now')
                FROM sources s JOIN documents d ON d.id=s.doc_id JOIN metadata_policies p ON p.collection=s.collection
                WHERE s.id=new.id AND s.source_uuid IS NOT NULL AND s.status='indexed'
                AND NOT EXISTS(SELECT 1 FROM metadata_jobs existing WHERE existing.source_uuid=s.source_uuid AND existing.content_hash=d.text_hash AND existing.profile_hash=p.profile_hash)
                AND (p.mode='changed' OR NOT EXISTS(SELECT 1 FROM metadata_jobs j WHERE j.source_uuid=s.source_uuid AND j.status IN ('extracted','needs_review','accepted','unknown')));
              END''')
        for table in ('sources','documents','passages','locations','extraction_cache','metadata_profiles','metadata_policies','metadata_jobs','metadata_values','metadata_overrides','metadata_runs'):
            for event in ('INSERT','UPDATE','DELETE'):
                condition=''
                if table=='sources' and event=='UPDATE':
                    fields=('collection','source_key','doc_id','title','locator','byte_hash','added_at','added_basis','modified_at','published_at','date_precision','status','detail')
                    condition=' WHEN '+' OR '.join(f'old.{f} IS NOT new.{f}' for f in fields)
                db.execute(f'CREATE TRIGGER IF NOT EXISTS managed_{table}_{event.lower()} AFTER {event} ON {table}{condition} BEGIN UPDATE library_management SET revision=revision+1 WHERE id=1; END')
        db.execute("UPDATE settings SET value='2' WHERE key='schema'")
        db.execute('UPDATE library_management SET revision=revision+1 WHERE id=1')
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Migration foreign-key validation failed')
        db.commit();library.metadata_enabled=True
    except BaseException:db.rollback();raise
    return {**preview,'applied':True,'backup':str(backup_path),'metadata_schema':'1'}


def register(db,profile):
    validate_profile(profile);key=profile_hash(profile)
    from .metadata_profile import instructions
    db.execute('INSERT OR IGNORE INTO metadata_profiles VALUES(?,?,?,?,?)',(key,profile['name'],canonical(profile),instructions(profile),time.time()))
    return key


def enqueue(db,source_id,key,force=False):
    source=db.execute('SELECT s.source_uuid,d.text_hash FROM sources s JOIN documents d ON d.id=s.doc_id WHERE s.id=? AND s.status=\'indexed\'',(source_id,)).fetchone()
    if source is None:return None
    identity,content_hash=source
    job=db.execute('SELECT id,status FROM metadata_jobs WHERE source_uuid=? AND content_hash=? AND profile_hash=?',(identity,content_hash,key)).fetchone()
    if job:
        if force:db.execute("UPDATE metadata_jobs SET status='pending',updated_at=? WHERE id=?",(time.time(),job[0]))
        return job[0]
    ident=str(uuid.uuid4());now=time.time()
    db.execute('INSERT INTO metadata_jobs(id,source_uuid,source_id,content_hash,profile_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,\'pending\',?,?)',(ident,identity,source_id,content_hash,key,now,now))
    return ident


def current_source(db,ident):
    row=db.execute('SELECT s.id,s.source_uuid,d.text_hash FROM sources s LEFT JOIN documents d ON d.id=s.doc_id WHERE s.id=?',(ident,)).fetchone()
    return dict(zip(('source_id','source_uuid','text_hash'),row)) if row else None


def commit_result(library,job,result,profile):
    db=library.db
    with nullcontext() if db.in_transaction else db:
        current=current_source(db,job['source_id'])
        if not current or current['source_uuid']!=job['source_uuid'] or current['text_hash']!=job['content_hash']:
            result['status']='stale'
            db.execute("UPDATE metadata_jobs SET status='stale',updated_at=? WHERE id=?",(time.time(),job['id']))
            db.execute('INSERT INTO metadata_runs VALUES(?,?,?,?,?)',(str(uuid.uuid4()),job['id'],job.get('model_hash'),canonical(result),time.time()))
            return 'stale'
        state=result['status'];metadata=result.get('metadata')
        db.execute('UPDATE metadata_jobs SET status=?,attempts=?,result=?,evidence=?,issues=?,updated_at=? WHERE id=?',
                   (state,canonical(result['attempts']),canonical(metadata) if metadata is not None else None,canonical(result.get('excerpts',[])),canonical(result.get('issues',[])),time.time(),job['id']))
        if metadata is not None:
            result['committed_fields']=[];result['preserved_manual_fields']=[]
            from .metadata_profile import parse_and_validate
            try:
                _,fresh_issues=parse_and_validate(canonical(metadata),profile,result.get('excerpts',[]))
                bad={issue.split(':',1)[0] for issue in fresh_issues}
            except (ValueError,TypeError,KeyError):bad={field['name'] for field in profile['fields']}
            if state in ('failed','cancelled'):bad={field['name'] for field in profile['fields']}
            for field in profile['fields']:
                name=field['name'];value=metadata[name]
                if name in bad or value is None:continue
                if db.execute('SELECT 1 FROM metadata_overrides WHERE source_uuid=? AND field=?',(job['source_uuid'],name)).fetchone():
                    result['preserved_manual_fields'].append(name);continue
                status='accepted' if state=='accepted' else 'needs_review' if state=='needs_review' or not field.get('auto_accept',False) else 'accepted'
                db.execute('''INSERT INTO metadata_values VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_uuid,field) DO UPDATE SET
                  content_hash=excluded.content_hash,profile_hash=excluded.profile_hash,value=excluded.value,status=excluded.status,
                  origin=excluded.origin,job_id=excluded.job_id,updated_at=excluded.updated_at''',
                  (job['source_uuid'],name,job['content_hash'],job['profile_hash'],canonical(value),status,'generated' if field.get('generated') else 'extracted',job['id'],time.time()))
                result['committed_fields'].append(name)
        db.execute('INSERT INTO metadata_runs VALUES(?,?,?,?,?)',(str(uuid.uuid4()),job['id'],job.get('model_hash'),canonical(result),time.time()))
        return state


def restore(library,backup_path,apply=False):
    if not apply:return _restore(library,backup_path,False)
    from .library_guard import maintenance
    with maintenance(library):return _restore(library,backup_path,True)


def _restore(library,backup_path,apply=False):
    from .sqlite_paths import connect_readonly,network_path
    backup_path=Path(backup_path).resolve()
    if network_path(backup_path):raise ValueError('Copy the closed backup locally before restoring it')
    if backup_path==library.path.resolve():raise ValueError('Restore source must differ from the working library')
    source=connect_readonly(backup_path)
    try:
        info=inspect(source)
        if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Backup validation failed')
        result={'source':str(backup_path),'destination':str(library.path),'backup_info':info,'applied':False}
        if not apply:return result
        db=library.db
        if db.in_transaction:raise ValueError('Finish the current transaction before restore')
        timeout=db.execute('PRAGMA busy_timeout').fetchone()[0];db.execute('PRAGMA busy_timeout=1000');db.execute('PRAGMA locking_mode=EXCLUSIVE')
        try:
            db.execute('BEGIN EXCLUSIVE');db.commit()
            identity=db.execute('SELECT uuid,revision FROM library_management WHERE id=1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='library_management'").fetchone() else None
            saved_identity=source.execute('SELECT uuid,revision FROM library_management WHERE id=1').fetchone() if source.execute("SELECT 1 FROM sqlite_master WHERE name='library_management'").fetchone() else None
            if identity and saved_identity and identity[0]!=saved_identity[0]:raise ValueError('Backup belongs to a different library; use import rather than restore')
            recovery=library.path.parent/('before-restore-'+uuid.uuid4().hex+'.db')
            copy=sqlite3.connect(recovery)
            try:db.backup(copy)
            finally:copy.close()
            source.backup(db)
            if identity and saved_identity:
                manager=getattr(library,'management',None)
                with db:db.execute('UPDATE library_management SET revision=? WHERE id=1',(max(identity[1],saved_identity[1],manager.state['synced_revision'] if manager else 0)+1,))
            library.metadata_enabled=migrated(db)
        except sqlite3.OperationalError as error:
            if 'locked' in str(error).lower():raise ValueError('Close the app and other readers/workers before restore') from error
            raise
        finally:
            if db.in_transaction:db.rollback()
            db.execute('PRAGMA locking_mode=NORMAL')
            db.execute('BEGIN');db.execute('SELECT value FROM settings LIMIT 1').fetchall();db.commit()
            db.execute(f'PRAGMA busy_timeout={timeout}')
        manager=getattr(library,'management',None)
        if manager is not None:
            library.close();manager._upgrade()
            if not saved_identity:
                reopened=sqlite3.connect(manager.path)
                try:
                    with reopened:reopened.execute('UPDATE library_management SET uuid=?,revision=? WHERE id=1',(identity[0] if identity else str(uuid.uuid4()),max(identity[1] if identity else 0,manager.state['synced_revision'])+1))
                finally:reopened.close()
            manager.state['activation']=str(uuid.uuid4());manager._save()
        return {**result,'applied':True,'recovery_backup':str(recovery)}
    finally:source.close()
