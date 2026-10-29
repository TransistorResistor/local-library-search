"""Local working libraries and recoverable single-user snapshot publication."""
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .sqlite_paths import connect_readonly,network_path


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)


def validate(path):
    db=connect_readonly(path,immutable=True)
    try:
        row=db.execute("SELECT value FROM settings WHERE key='schema'").fetchone()
        if not row or row[0] not in ('1','2'):raise ValueError('Not a supported search library')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Library integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Library foreign-key check failed')
        required={'sources','documents','passages','locations','passage_fts','title_fts'}
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required<=tables:raise ValueError('Library is missing required tables')
    finally:db.close()


def refuse_sidecars(path):
    if any(Path(str(path)+s).exists() for s in ('-wal','-journal')):
        raise ValueError('Network library has WAL/journal files. Close/checkpoint its writer or provide a completed export; do not delete sidecars.')


@contextmanager
def publication_lock(path):
    lock=Path(str(path)+'.publish-lock')
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise ValueError('Another publisher or an interrupted publication owns '+str(lock)+'. Verify no publisher is running before manually removing a stale lock.') from None
    try:
        with os.fdopen(fd,'w') as stream:stream.write(json.dumps({'pid':os.getpid(),'created_at':time.time()}))
        yield
    finally:lock.unlink(missing_ok=True)


class ManagedLibrary:
    """One local owner; remote conflicts never overwrite pending local changes."""
    def __init__(self,origin,root=None,source_paths=()):
        self.origin=Path(origin).expanduser().resolve()
        if any(self.origin==Path(p).resolve() for p in source_paths):raise ValueError('Network destination must differ from source databases')
        base=Path(root) if root else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'LocalLibrarySearch/libraries'
        if network_path(base.resolve()):raise ValueError('AppData working-library root must be on local storage')
        ident=hashlib.sha256(os.path.normcase(str(self.origin)).encode()).hexdigest()[:24]
        self.directory=base/ident;self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'library.db';self.state_path=self.directory/'sync-state.json'
        self.session=(self.directory/'session.lock').open('a+b')
        try:
            self.session.seek(0);self.session.write(b'0');self.session.flush();self.session.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.session.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.session,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.session.close();raise ValueError('This local working library is already in use by another app/command') from None
        try:
            self.state=json.loads(self.state_path.read_text('utf-8')) if self.state_path.exists() else {'origin':str(self.origin),'baseline':None,'synced_revision':-1,'last_success':None}
            if self.state.get('origin')!=str(self.origin):raise ValueError('Library registry origin mismatch')
            if not self.path.exists():
                if self.origin.exists():self._import()
                else:
                    from .store import Library
                    with Library(self.path):pass
            self._upgrade();self.last_attempt=0;self.retry_at=0;self.failures=0;self.last_revision=self.revision();self.changed_at=time.monotonic();self.dirty_since=self.changed_at
        except Exception:self.close();raise

    def close(self):
        if getattr(self,'session',None):self.session.close();self.session=None

    def _save(self):atomic_json(self.state_path,self.state)

    def _import(self):
        from .library_guard import LibraryGuard
        with LibraryGuard(self.path,exclusive=True):self._import_locked()

    def _import_locked(self):
        refuse_sidecars(self.origin)
        before=digest(self.origin);stage=self.directory/'import.tmp'
        try:
            shutil.copyfile(self.origin,stage);refuse_sidecars(self.origin)
            if digest(stage)!=before or digest(self.origin)!=before:raise ValueError('Network library changed during import')
            validate(stage)
            imported=connect_readonly(stage,immutable=True)
            try:
                version=imported.execute("SELECT value FROM settings WHERE key='schema'").fetchone()[0]
                synced_revision=imported.execute('SELECT revision FROM library_management WHERE id=1').fetchone()[0] if version=='2' else -1
            finally:imported.close()
            if self.path.exists():
                local=sqlite3.connect(self.path,timeout=1);backup=sqlite3.connect(self.directory/'before-network-import.db')
                try:
                    if local.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:raise ValueError('Local library is busy; close readers before adopting a snapshot')
                    if local.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower()!='delete':raise ValueError('Cannot release local WAL for snapshot adoption')
                    local.backup(backup)
                finally:backup.close();local.close()
                if any(Path(str(self.path)+s).exists() for s in ('-wal','-journal')):raise ValueError('Close the local library before adopting a published snapshot')
            os.replace(stage,self.path)
            self.state.pop('operation',None)
            self.state.update(baseline=before,synced_revision=synced_revision,activation=str(uuid.uuid4()));self._save()
        finally:stage.unlink(missing_ok=True)

    def _upgrade(self):
        db=sqlite3.connect(self.path)
        try:
            version=db.execute("SELECT value FROM settings WHERE key='schema'").fetchone()
            if not version or version[0] not in ('1','2'):raise ValueError('Unsupported search library')
            if version[0]=='1':
                backup=sqlite3.connect(self.directory/'before-management.db')
                try:db.backup(backup)
                finally:backup.close()
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS library_management(id INTEGER PRIMARY KEY CHECK(id=1),uuid TEXT NOT NULL,revision INTEGER NOT NULL)')
                db.execute('INSERT OR IGNORE INTO library_management VALUES(1,?,0)',(str(uuid.uuid4()),))
                for table in ('sources','documents','passages','locations','extraction_cache'):
                    for event in ('INSERT','DELETE','UPDATE'):
                        condition=''
                        if table=='sources' and event=='UPDATE':
                            fields=('collection','source_key','doc_id','title','locator','byte_hash','added_at','added_basis','modified_at','published_at','date_precision','status','detail')
                            condition=' WHEN '+' OR '.join(f'old.{f} IS NOT new.{f}' for f in fields)
                        db.execute(f'CREATE TRIGGER IF NOT EXISTS managed_{table}_{event.lower()} AFTER {event} ON {table}{condition} BEGIN UPDATE library_management SET revision=revision+1 WHERE id=1; END')
                db.execute("UPDATE settings SET value='2' WHERE key='schema'")
            self.state.setdefault('activation',str(uuid.uuid4()));self._save()
        finally:db.close()

    def revision(self):
        db=connect_readonly(self.path)
        try:return db.execute('SELECT revision FROM library_management WHERE id=1').fetchone()[0]
        finally:db.close()

    def status(self):
        revision=self.revision()
        return {'local_path':str(self.path),'network_path':str(self.origin),'revision':revision,
                'pending':revision!=self.state['synced_revision'],'last_success':self.state.get('last_success'),
                'error':self.state.get('error'),'activation':self.state.get('activation')}

    def adopt_published(self,preserve_pending=False):
        """Caller must release its local library connection first."""
        if self.status()['pending'] and not preserve_pending:raise ValueError('Local changes are pending. Sync them or explicitly preserve them in a recovery backup before adopting the published library.')
        with publication_lock(self.origin):self._import()
        self._upgrade();self.state.pop('error',None);self._save()
        self.last_revision=self.revision();self.changed_at=time.monotonic();self.dirty_since=self.changed_at
        return self.status()

    def sync(self,cancel_event=None):
        self.last_attempt=time.monotonic();snapshot=self.directory/'upload.db';source=None;target=None
        try:
            source=connect_readonly(self.path);target=sqlite3.connect(snapshot)
            snapshot_deadline=time.monotonic()+120
            def progress(*args):
                if cancel_event is not None and cancel_event.is_set():raise ValueError('Sync cancelled; local changes retained')
                if time.monotonic()>=snapshot_deadline:raise ValueError('Snapshot time limit exceeded; local changes retained')
            source.backup(target,pages=256,progress=progress);target.close();target=None;source.close();source=None
            validate(snapshot)
            db=connect_readonly(snapshot,immutable=True)
            try:revision=db.execute('SELECT revision FROM library_management WHERE id=1').fetchone()[0]
            finally:db.close()
            fingerprint=digest(snapshot)
            # Parent accessibility is tested even when publishing a new file.
            if not self.origin.parent.is_dir():raise ValueError('Network destination directory is unavailable')
            with publication_lock(self.origin):
                refuse_sidecars(self.origin)
                current=digest(self.origin) if self.origin.exists() else None
                operation=self.state.get('operation')
                if operation and current==operation['hash']:
                    self.state.update(baseline=current,synced_revision=operation['revision'],last_success=time.time());self.state.pop('operation',None);self._save()
                if current!=self.state['baseline']:raise ValueError('Sync conflict: network library changed independently. Both copies retained; no upload performed.')
                if revision==self.state['synced_revision'] and current is not None:
                    self.state.pop('error',None);self._save();self.failures=0;return self.status()
                token=uuid.uuid4().hex;remote_temp=self.origin.with_name(self.origin.name+'.upload-'+token)
                try:
                    with snapshot.open('rb') as src,remote_temp.open('xb') as dst:
                        for block in iter(lambda:src.read(1024*1024),b''):
                            progress();dst.write(block)
                        dst.flush();os.fsync(dst.fileno())
                    if digest(remote_temp)!=fingerprint:raise ValueError('Uploaded snapshot checksum mismatch')
                    refuse_sidecars(self.origin)
                    if (digest(self.origin) if self.origin.exists() else None)!=current:raise ValueError('Network library changed during upload; both copies retained')
                    if self.origin.exists():
                        previous=self.origin.with_name(self.origin.name+'.previous')
                        prior_stage=self.origin.with_name(self.origin.name+'.previous-'+token)
                        try:shutil.copyfile(self.origin,prior_stage);os.replace(prior_stage,previous)
                        finally:prior_stage.unlink(missing_ok=True)
                    self.state['operation']={'hash':fingerprint,'revision':revision,'id':token};self._save()
                    os.replace(remote_temp,self.origin)
                    if digest(self.origin)!=fingerprint:raise ValueError('Published snapshot verification failed; recovery state retained')
                    self.state.update(baseline=fingerprint,synced_revision=revision,last_success=time.time());self.state.pop('operation',None);self.state.pop('error',None);self._save()
                finally:remote_temp.unlink(missing_ok=True)
            self.failures=0;self.retry_at=0;self.dirty_since=time.monotonic();return self.status()
        except Exception as error:
            self.failures+=1;self.retry_at=time.monotonic()+min(300,10*2**min(self.failures,5))
            self.state['error']=str(error);self._save();raise
        finally:
            if target is not None:target.close()
            if source is not None:source.close()
            snapshot.unlink(missing_ok=True)

    def due(self,quiet_seconds=60,max_pending_seconds=300):
        now=time.monotonic();revision=self.revision()
        if revision!=self.last_revision:
            if self.last_revision==self.state['synced_revision']:self.dirty_since=now
            self.last_revision=revision;self.changed_at=now
        return revision!=self.state['synced_revision'] and now>=self.retry_at and (now-self.changed_at>=quiet_seconds or now-self.dirty_since>=max_pending_seconds)


@contextmanager
def configured_library(config):
    from .store import Library
    from .sqlite_paths import network_path
    manager=None
    try:
        if config.get('library_sync',{}).get('enabled',False) or network_path(config['database']):
            manager=ManagedLibrary(config['database'],root=config.get('library_sync',{}).get('local_root'),source_paths=[s['path'] for s in config['sources']])
        with Library(manager.path if manager else config['database'],**config.get('database_options',{})) as library:
            library.management=manager
            yield library
    finally:
        if manager:manager.close()
