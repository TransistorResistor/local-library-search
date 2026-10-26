"""Persistent FTS5 index with canonical documents, passages and source occurrences."""
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, text_hash TEXT UNIQUE NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id INTEGER PRIMARY KEY, collection TEXT NOT NULL, source_key TEXT NOT NULL,
 doc_id INTEGER REFERENCES documents(id), title TEXT NOT NULL, locator TEXT NOT NULL,
 byte_hash TEXT, added_at TEXT, added_basis TEXT, modified_at TEXT, published_at TEXT,
 date_precision TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL, UNIQUE(collection,source_key));
CREATE INDEX IF NOT EXISTS sources_doc ON sources(doc_id);
CREATE INDEX IF NOT EXISTS sources_dates ON sources(added_at,published_at);
CREATE VIRTUAL TABLE IF NOT EXISTS title_fts USING fts5(title,tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY,text_hash TEXT UNIQUE NOT NULL,text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS locations(doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 passage_id INTEGER NOT NULL REFERENCES passages(id),page INTEGER,start_char INTEGER NOT NULL,end_char INTEGER NOT NULL,
 PRIMARY KEY(doc_id,start_char));
CREATE INDEX IF NOT EXISTS locations_passage ON locations(passage_id,doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(text,tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS extraction_cache(byte_hash TEXT NOT NULL,policy TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(byte_hash,policy));
'''


class Library:
    def __init__(self, path, cache_mb=32):
        started=time.perf_counter()
        cache_mb=int(cache_mb)
        if not 1<=cache_mb<=1024:raise ValueError('Database cache must be 1..1024 MiB per connection')
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.execute(f'PRAGMA cache_size={-cache_mb*1024}')
        self.db.execute('PRAGMA foreign_keys=ON')
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables:
            owned=self.db.execute("SELECT value FROM settings WHERE key='schema'").fetchone() if 'settings' in tables else None
            if not owned or owned[0]!='1':
                self.db.close();raise ValueError('Destination is not a local_search library; choose a new database file')
        # Current libraries need no writes or full title-index scan on query opens.
        if tables and 'title_fts' in tables:
            self.setup_seconds=time.perf_counter()-started
            return
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        version=self.db.execute("SELECT value FROM settings WHERE key='schema'").fetchone()
        if version and version[0]!='1':raise ValueError('Unsupported library schema')
        self.db.execute("INSERT OR IGNORE INTO settings VALUES('schema','1')")
        self.db.execute('INSERT INTO title_fts(rowid,title) SELECT id,title FROM sources WHERE id NOT IN (SELECT rowid FROM title_fts)')
        self.db.commit()
        self.setup_seconds=time.perf_counter()-started

    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()

    def previous(self,collection,key):
        row=self.db.execute('SELECT * FROM sources WHERE collection=? AND source_key=?',(collection,key)).fetchone()
        return dict(row) if row else None

    def put(self, collection, key, title, locator, segments, byte_hash=None, added_at=None,
            added_basis=None, modified_at=None, published_at=None, date_precision=None,
            status='indexed',detail=None):
        now=utc_now();previous=self.previous(collection,key)
        first=previous['first_seen'] if previous else now
        if not added_at:added_at=previous['added_at'] if previous else first;added_basis=previous['added_basis'] if previous else 'index_first_seen'
        text='\n\n'.join(s['text'] for s in segments)
        doc_id=None
        if status=='indexed' and text.strip():
            # Keep original page/offset geometry. Different layouts still share indexed passages.
            text_hash=sha(json.dumps(segments,ensure_ascii=False,sort_keys=True))
            row=self.db.execute('SELECT id FROM documents WHERE text_hash=?',(text_hash,)).fetchone()
            if row:doc_id=row[0]
            else:
                doc_id=self.db.execute('INSERT INTO documents(text_hash,text) VALUES(?,?)',(text_hash,text)).lastrowid
                offset=0
                for segment in segments:
                    body=segment['text']
                    # Paragraph boundaries allow repeated paragraphs to collapse across different files.
                    for match in re.finditer(r'\S(?:.*?\S)?(?=\n\s*\n|\Z)',body,re.S):
                        block=match.group(); words=list(re.finditer(r'\S+',block))
                        for start in range(0,len(words),220):
                            first_word=words[start].start();last_word=words[min(start+220,len(words))-1].end()
                            raw=block[first_word:last_word];normalized=canonical(raw);h=sha(normalized)
                            row=self.db.execute('SELECT id FROM passages WHERE text_hash=?',(h,)).fetchone()
                            if row:pid=row[0]
                            else:
                                pid=self.db.execute('INSERT INTO passages(text_hash,text) VALUES(?,?)',(h,normalized)).lastrowid
                                self.db.execute('INSERT INTO passage_fts(rowid,text) VALUES(?,?)',(pid,normalized))
                            self.db.execute('INSERT INTO locations VALUES(?,?,?,?,?)',(doc_id,pid,segment.get('page'),offset+match.start()+first_word,offset+match.start()+last_word))
                    offset+=len(body)+2
        elif status=='indexed':status='skipped';detail={'reason':'empty text'}
        self.db.execute('''INSERT INTO sources(collection,source_key,doc_id,title,locator,byte_hash,added_at,added_basis,
          modified_at,published_at,date_precision,first_seen,last_seen,status,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(collection,source_key) DO UPDATE SET doc_id=excluded.doc_id,title=excluded.title,locator=excluded.locator,
          byte_hash=excluded.byte_hash,added_at=excluded.added_at,added_basis=excluded.added_basis,modified_at=excluded.modified_at,
          published_at=excluded.published_at,date_precision=excluded.date_precision,last_seen=excluded.last_seen,status=excluded.status,detail=excluded.detail''',
          (collection,key,doc_id,title,locator,byte_hash,added_at,added_basis,modified_at,published_at,date_precision,first,now,status,json.dumps(detail or {},ensure_ascii=False)))
        source_id=self.db.execute('SELECT id FROM sources WHERE collection=? AND source_key=?',(collection,key)).fetchone()[0]
        self.db.execute('DELETE FROM title_fts WHERE rowid=?',(source_id,))
        self.db.execute('INSERT INTO title_fts(rowid,title) VALUES(?,?)',(source_id,title))
        return doc_id

    def prune(self,collection,seen):
        # Only called after a complete, successful source traversal.
        removed=0
        for row in self.db.execute('SELECT id,source_key FROM sources WHERE collection=?',(collection,)).fetchall():
            if row['source_key'] not in seen:
                self.db.execute('DELETE FROM title_fts WHERE rowid=?',(row['id'],))
                self.db.execute('DELETE FROM sources WHERE id=?',(row['id'],));removed+=1
        self.gc();return removed

    def gc(self):
        self.db.execute('DELETE FROM documents WHERE id NOT IN (SELECT doc_id FROM sources WHERE doc_id IS NOT NULL)')
        orphan=[r[0] for r in self.db.execute('SELECT id FROM passages WHERE id NOT IN (SELECT passage_id FROM locations)')]
        for pid in orphan:
            self.db.execute('DELETE FROM passage_fts WHERE rowid=?',(pid,));self.db.execute('DELETE FROM passages WHERE id=?',(pid,))
        self.db.execute('DELETE FROM extraction_cache WHERE byte_hash NOT IN (SELECT byte_hash FROM sources WHERE byte_hash IS NOT NULL)')

    def status(self):
        return dict(documents=self.db.execute('SELECT count(*) FROM documents').fetchone()[0],
                    unique_passages=self.db.execute('SELECT count(*) FROM passages').fetchone()[0],
                    passage_occurrences=self.db.execute('SELECT count(*) FROM locations').fetchone()[0],
                    sources=[dict(r) for r in self.db.execute('SELECT collection,status,count(*) AS count FROM sources GROUP BY collection,status')])

    def catalog(self,offset=0,limit=20,status=None,title_query=None):
        offset=max(0,int(offset));limit=max(1,min(50,int(limit)))
        clauses=['status=?'] if status else [];params=[status] if status else []
        if title_query:clauses.append('id IN (SELECT rowid FROM title_fts WHERE title_fts MATCH ?)');params.append(title_query)
        where='WHERE '+' AND '.join(clauses) if clauses else ''
        return [dict(r) for r in self.db.execute(f'SELECT id,doc_id,title,locator,collection,added_at,added_basis,modified_at,published_at,date_precision,status,detail FROM sources {where} ORDER BY id LIMIT ? OFFSET ?',params+[limit,offset])]

    def sources_for(self,doc_id):
        return [dict(r) for r in self.db.execute('SELECT id,title,locator,collection,added_at,added_basis,modified_at,published_at,date_precision FROM sources WHERE doc_id=? ORDER BY added_at DESC,id',(doc_id,))]

    @staticmethod
    def date_filter(date_field,after,before,collection):
        if date_field not in ('added_at','modified_at','published_at'):raise ValueError('Invalid date field')
        clauses=["s.status='indexed'"];params=[]
        for op,value in [('>=',after),('<',before)]:
            if value:
                if not isinstance(value,str):raise ValueError('Date filters must be ISO date strings')
                # A date means UTC midnight; before is exclusive.
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
                clauses.append(f's.{date_field}{op}?');params.append(parsed.astimezone(timezone.utc).isoformat(timespec='seconds'))
        if collection:clauses.append('s.collection=?');params.append(collection)
        return ' AND '.join(clauses),params

    def search(self,query,limit=5,offset=0,date_field='added_at',after=None,before=None,collection=None,sort='relevance',exact_count=True):
        started=time.perf_counter()
        if not isinstance(query,str) or not query.strip() or len(query)>4000:raise ValueError('query must be a nonempty FTS5 string of at most 4000 characters')
        if collection is not None and not isinstance(collection,str):raise ValueError('collection must be a string')
        if sort not in ('relevance','newest'):raise ValueError('sort must be relevance or newest')
        limit=max(1,min(20,int(limit)));offset=max(0,int(offset))
        where,params=self.date_filter(date_field,after,before,collection)
        base=f'''FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid
        WHERE passage_fts MATCH ? AND EXISTS(SELECT 1 FROM locations l JOIN sources s ON s.doc_id=l.doc_id
        WHERE l.passage_id=p.id AND {where})'''
        count=self.db.execute('SELECT count(*) '+base,[query]+params).fetchone()[0] if exact_count else None
        counted=time.perf_counter()
        date_expr=f'(SELECT max(s.{date_field}) FROM locations l JOIN sources s ON s.doc_id=l.doc_id WHERE l.passage_id=p.id AND {where})'
        # SELECT placeholders precede WHERE placeholders.
        order='newest DESC,rank,p.id' if sort=='newest' else 'rank,p.id'
        rows=self.db.execute(f"SELECT p.id,p.text,bm25(passage_fts) AS rank,{date_expr} AS newest "+base+f' ORDER BY {order} LIMIT ? OFFSET ?',params+[query]+params+[limit+1,offset]).fetchall()
        has_more=len(rows)>limit;rows=rows[:limit];ranked=time.perf_counter()
        hits=[]
        for r in rows:
            locs=self.db.execute(f'''SELECT DISTINCT l.doc_id,l.page,l.start_char,l.end_char,s.id AS source_id,s.title,s.locator,
              s.added_at,s.added_basis,s.modified_at,s.published_at,s.date_precision,s.collection
              FROM locations l JOIN sources s ON s.doc_id=l.doc_id WHERE l.passage_id=? AND {where}
              ORDER BY s.{date_field} DESC,s.id LIMIT 20''',[r['id']]+params).fetchall()
            total=self.db.execute(f'SELECT count(*) FROM locations l JOIN sources s ON s.doc_id=l.doc_id WHERE l.passage_id=? AND {where}',[r['id']]+params).fetchone()[0]
            hits.append(dict(passage_id=r['id'],text=r['text'],rank=r['rank'],newest=r['newest'],locations=[dict(x) for x in locs],location_count=total))
        return dict(query=query,total_unique_passages=count,results=hits,next_offset=offset+len(hits) if has_more else None,
                    timings=dict(count_seconds=counted-started,rank_seconds=ranked-counted,metadata_seconds=time.perf_counter()-ranked,total_seconds=time.perf_counter()-started),
                    date_field=date_field,date_note='Date added/captured is not publication date. Missing dates are excluded by date filters.')

    def read(self,doc_id,start_char=0,count_chars=4000):
        row=self.db.execute('SELECT text FROM documents WHERE id=?',(int(doc_id),)).fetchone()
        if not row:raise ValueError('Unknown document')
        start=max(0,int(start_char));count=max(1,min(16000,int(count_chars)));text=row[0];end=min(len(text),start+count)
        return dict(doc_id=doc_id,start_char=start,end_char=end,total_chars=len(text),text=text[start:end],
                    next_start=end if end<len(text) else None,sources=self.sources_for(doc_id)[:20])

    def context(self,passage_id,doc_id=None,radius_chars=1000):
        if doc_id is None:
            candidates=[r[0] for r in self.db.execute('SELECT DISTINCT doc_id FROM locations WHERE passage_id=? ORDER BY doc_id LIMIT 21',(int(passage_id),))]
            if len(candidates)>1:raise ValueError('This repeated passage has different document contexts. Choose doc_id from your search result: '+str(candidates[:20]))
        sql='SELECT * FROM locations WHERE passage_id=?';params=[int(passage_id)]
        if doc_id is not None:sql+=' AND doc_id=?';params.append(int(doc_id))
        row=self.db.execute(sql+' ORDER BY doc_id,start_char LIMIT 1',params).fetchone()
        if not row:raise ValueError('Unknown passage/document location')
        radius=max(0,min(6000,int(radius_chars)))
        result=self.read(row['doc_id'],max(0,row['start_char']-radius),row['end_char']-max(0,row['start_char']-radius)+radius)
        result.update(passage_id=passage_id,page=row['page']);return result
