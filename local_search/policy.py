"""Bounded public evidence checklist and answer-effort settings."""
MODES={'legacy':24,'quick':6,'balanced':12,'thorough':24}

GUIDANCE='''
Maintain a concise evidence checklist using optional "coverage" on any action. It replaces the previous checklist:
[{"need":"Requested fact","status":"supported","evidence_ids":[1]}].
Statuses: needed, supported, conflicting, unresolved. References are delivered evidence_id values, not source IDs.
Supported/conflicting items require references. Cover every requested part; unresolved is an honest outcome after proportionate searching.
This is a short evidence/status record, not private reasoning. Keep at most 12 needs and use short evidence items.
MANDATORY EARLY-ANSWER CHECK: Before the forced final round, answer requires a nonempty complete coverage list already retained by the host or attached to that answer. No entries may remain needed. Answer text and gaps do NOT replace coverage. If none has been supplied, attach coverage to the answer without a separate planning turn. Mark missing information unresolved and retain every requested part because an update replaces the entire list.
Answer when each material need is supported or explicitly unresolved; describe conflicts and gaps. One direct source can suffice.
Compare both sides of comparisons, distinguish publication/information dates from capture dates, and do not count copies as corroboration.
Additional retrieval should address a concrete gap or contradiction. Do not repeat searches merely to confirm the same answer.
At finalisation return only an answer from visible evidence, with honest unknowns. Cite source IDs present in that evidence.
You may include "gaps":["Unresolved point"] on answer. Checklist completion is your assessed coverage, not a correctness certification.
'''


def coverage_update(value,exposed):
    if not isinstance(value,list) or not 1<=len(value)<=12:raise ValueError('coverage must contain 1..12 evidence needs')
    result=[]
    for item in value:
        if not isinstance(item,dict) or set(item)-{'need','status','evidence_ids'}:raise ValueError('Each coverage item accepts need, status and evidence_ids')
        need=item.get('need');status=item.get('status');ids=item.get('evidence_ids',[])
        if not isinstance(need,str) or not need.strip() or len(need)>256:raise ValueError('Coverage need must be 1..256 characters')
        if status not in ('needed','supported','conflicting','unresolved'):raise ValueError('Invalid coverage status')
        if not isinstance(ids,list) or len(ids)>8 or any(type(i) is not int or i not in exposed for i in ids):raise ValueError('Coverage references must be previously delivered evidence IDs (at most 8 per need)')
        if status in ('supported','conflicting') and not ids:raise ValueError('Supported/conflicting needs require evidence references')
        result.append({'need':need.strip(),'status':status,'evidence_ids':list(dict.fromkeys(ids))})
    return result


def source_ids(value):
    found=set()
    if isinstance(value,dict):
        if type(value.get('source_id')) is int:found.add(value['source_id'])
        for source in value.get('sources',[]):
            if isinstance(source,dict) and type(source.get('id')) is int:found.add(source['id'])
        for child in value.values():found.update(source_ids(child))
    elif isinstance(value,list):
        for child in value:found.update(source_ids(child))
    return found


class RetrievalProgress:
    def __init__(self):self.passages=set();self.ranges={};self.sources=set();self.stalled=0
    def observe(self,name,result):
        from .store import canonical,sha
        novel=False
        if isinstance(result,dict):
            for hit in result.get('results',[]):
                if 'text' in hit and 'passage_id' in hit:
                    ident=sha(canonical(hit['text']))
                    if ident not in self.passages:novel=True;self.passages.add(ident)
                    for location in hit.get('locations',[]):
                        if all(key in location for key in ('doc_id','start_char','end_char')):
                            self.ranges.setdefault(location['doc_id'],[]).append((location['start_char'],location['end_char']))
                elif 'id' in hit:
                    if hit['id'] not in self.sources:novel=True;self.sources.add(hit['id'])
            if result.get('text') and 'doc_id' in result:
                start=result.get('start_char',0);end=result.get('end_char',start+len(result['text']))
                spans=self.ranges.setdefault(result['doc_id'],[])
                # Union coverage prevents repeated or overlapping slices being counted twice.
                cursor=start
                for left,right in sorted(spans):
                    if right<=cursor:continue
                    if left>cursor:break
                    cursor=max(cursor,right)
                novel=novel or cursor<end
                spans.append((start,end))
        self.stalled=0 if novel else self.stalled+1
        return novel
