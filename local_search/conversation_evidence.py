"""Bounded prior-turn excerpts, revalidated against the current question snapshot."""
import copy
import hashlib
import json
from .policy import source_ids


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def library_identity(library):
    if library.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_management'").fetchone():
        return library.db.execute('SELECT uuid FROM library_management WHERE id=1').fetchone()[0]
    return 'path:'+str(library.path)


def source_version(library,ident):
    row=library.db.execute('SELECT s.*,d.text_hash AS indexed_hash FROM sources s LEFT JOIN documents d ON d.id=s.doc_id WHERE s.id=?',(ident,)).fetchone()
    if not row or row['status']!='indexed' or row['indexed_hash'] is None:return None
    value=dict(row);value.pop('last_seen',None)
    return hashlib.sha256(packed(value).encode()).hexdigest()


def fragments(results,citations):
    cited=set(citations)
    for result in results:
        if not isinstance(result,dict):continue
        for hit in result.get('results',[]):
            if not isinstance(hit,dict) or not isinstance(hit.get('text'),str) or 'passage_id' not in hit:continue
            locations=[loc for loc in hit.get('locations',[]) if loc.get('source_id') in cited]
            if locations:yield {'results':[dict(copy.deepcopy(hit),locations=copy.deepcopy(locations))],'match_scope':'previous_turn_evidence'}
        if isinstance(result.get('text'),str) and result['text']:
            sources=[source for source in result.get('sources',[]) if source.get('id') in cited]
            if sources:yield dict(copy.deepcopy(result),sources=copy.deepcopy(sources))


def snapshot(library,results):
    items=[]
    for result in results:
        versions=[{'source_id':ident,'fingerprint':source_version(library,ident)} for ident in sorted(source_ids(result))]
        if versions and all(item['fingerprint'] for item in versions):items.append({'result':result,'versions':versions})
    return {'library':library_identity(library),'items':items}


def verified_body(library,result):
    if result.get('results'):
        for hit in result['results']:
            text=library.db.execute('SELECT text FROM passages WHERE id=?',(hit.get('passage_id'),)).fetchone()
            if not text or text[0]!=hit.get('text'):return False
            for loc in hit.get('locations',[]):
                row=library.db.execute('''SELECT 1 FROM locations l JOIN sources s ON s.doc_id=l.doc_id
                  WHERE s.id=? AND l.doc_id=? AND l.passage_id=? AND l.page IS ? AND l.start_char=? AND l.end_char=?''',
                  (loc.get('source_id'),loc.get('doc_id'),hit['passage_id'],loc.get('page'),loc.get('start_char'),loc.get('end_char'))).fetchone()
                if not row:return False
        return True
    if isinstance(result.get('text'),str) and type(result.get('start_char')) is int:
        text=library.db.execute('SELECT substr(text,?,?) FROM documents WHERE id=?',(result['start_char']+1,len(result['text']),result.get('doc_id'))).fetchone()
        if not text or text[0]!=result['text']:return False
        return all(library.db.execute('SELECT 1 FROM sources WHERE id=? AND doc_id=?',(source.get('id'),result.get('doc_id'))).fetchone() for source in result.get('sources',[]))
    return False


def restore(library,bundle):
    if not bundle:return [],0
    if not isinstance(bundle,dict) or bundle.get('library')!=library_identity(library):return [],len(bundle.get('items',[])) if isinstance(bundle,dict) else 1
    valid=[];dropped=0
    for item in bundle.get('items',[])[:8]:
        try:
            result=item['result'];versions=item['versions']
            if set(source_ids(result))!={v['source_id'] for v in versions} or not versions:raise ValueError('Missing source provenance')
            if any(source_version(library,v['source_id'])!=v['fingerprint'] for v in versions):raise ValueError('Source changed')
            if not verified_body(library,result):raise ValueError('Excerpt changed')
            valid.append(copy.deepcopy(result))
        except (ValueError,TypeError,KeyError):dropped+=1
    return valid,dropped
