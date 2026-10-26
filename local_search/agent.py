"""Small JSON tool loop; source excerpts persist as bounded notes across checkpoints."""
import json
import time
from .model import parse_action
from .prompts import load_prompt
import hashlib



def packed(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def ask(library,model,question,max_rounds=24,note_tokens=1500,on_event=None,prompts_file=None,reranker=None):
    started=time.perf_counter()
    stages={'model_seconds':0.0,'fts_search_seconds':0.0,'catalog_seconds':0.0,'context_seconds':0.0,'document_seconds':0.0,'notes_seconds':0.0,'other_tools_seconds':0.0}
    system_prompt=load_prompt('search.system',prompts_file)
    searcher=None
    if reranker is not None and reranker.config.enabled:
        from .rerank import QuestionSearch,GUIDANCE
        searcher=QuestionSearch(library,reranker,question,getattr(model,'cancel_event',None),on_event)
        system_prompt+='\n\n'+GUIDANCE
    owned=not library.db.in_transaction
    if owned:library.db.execute('BEGIN')
    try:
        result=_ask(library,model,question,max_rounds,note_tokens,on_event,system_prompt,stages,searcher)
        if searcher is not None:
            result['reranking']=searcher.trace
            from dataclasses import asdict
            result['reranker_settings']=asdict(reranker.config)
            # Split search-tool wall time into lexical SQL and reranking/shortlist work.
            stages['rerank_seconds']=min(stages['fts_search_seconds'],searcher.rerank_seconds)
            stages['fts_search_seconds']-=stages['rerank_seconds']
        result['prompt']={'section':'search.system','sha256':hashlib.sha256(system_prompt.encode('utf-8')).hexdigest()}
        ids=result.get('citations',[])
        result['sources']=[]
        for ident in dict.fromkeys(ids):
            row=library.db.execute('SELECT id,title,locator,collection,added_at,modified_at,published_at FROM sources WHERE id=?',(ident,)).fetchone()
            if row:result['sources'].append(dict(row))
    finally:
        if owned:library.db.rollback()
    total=time.perf_counter()-started
    stages['agent_processing_seconds']=max(0,total-sum(stages.values()))
    result['timings']={'total_seconds':total,'model_rounds':len(result['context_checks']),**stages}
    return result


def _ask(library,model,question,max_rounds,note_tokens,on_event,system_prompt,stages,searcher):
    recent=[];notes={};seen={};exposed=set();serial=0;trace=[];checks=[];evictions=0
    def messages():
        return [{'role':'system','content':system_prompt},{'role':'user','content':question},
                {'role':'user','content':packed({'saved_evidence':notes,'recent_results':recent,'evicted_results':evictions})}]
    for step in range(max_rounds):
        if getattr(model,'cancel_event',None) is not None and model.cancel_event.is_set():
            return dict(status='cancelled',answer=None,trace=trace,context_checks=checks)
        prompt=messages()
        while model.count(prompt)+model.output>model.context and recent:
            recent.pop(0);evictions+=1;prompt=messages()
        if model.count(prompt)+model.output>model.context:raise ValueError('Question and saved notes exceed context; shorten question or notebook budget')
        exposed.update(entry['evidence_id'] for entry in recent)
        exposed.update(notes)
        if on_event:on_event({'type':'round','round':step+1,'thinking':getattr(model,'thinking',False)})
        model_started=time.perf_counter()
        response=model.complete(prompt,on_event=on_event) if on_event else model.complete(prompt)
        stages['model_seconds']+=time.perf_counter()-model_started
        checks.append({'reserved_total':response['reserved_total'],'usage':response.get('usage',{}),'timings':response.get('timings',{}),'finish_reason':response['choices'][0].get('finish_reason')})
        if getattr(model,'cancel_event',None) is not None and model.cancel_event.is_set():
            return dict(status='cancelled',answer=None,trace=trace,context_checks=checks)
        if response['choices'][0].get('finish_reason')=='length':
            return dict(status='output_budget_exhausted',answer=None,trace=trace,context_checks=checks)
        name='invalid';action={};submitted=None;tool_started=None
        try:
            action=parse_action(response['choices'][0]['text']);submitted=dict(action);name=action.pop('action')
            if name=='answer':
                citations=action.get('citations',[])
                # Sources must have appeared in returned evidence, not just have guessed numeric IDs.
                allowed=set()
                def collect(value):
                    if isinstance(value,dict):
                        if type(value.get('source_id')) is int:allowed.add(value['source_id'])
                        for source in value.get('sources',[]):allowed.add(source['id'])
                        for item in value.values():collect(item)
                    elif isinstance(value,list):
                        for item in value:collect(item)
                for ident in exposed:collect(seen[ident])
                if not isinstance(citations,list) or any(type(i) is not int or i not in allowed for i in citations):
                    raise ValueError('Invalid citations '+packed(citations)+'. Use source IDs from retrieved evidence: '+packed(sorted(allowed)[:40])+'. Do not cite evidence_id, passage_id, or reference numbers inside source text.')
                if not isinstance(action.get('answer'),str):raise ValueError('answer must be text')
                return dict(status='answered',answer=action['answer'],citations=citations,trace=trace,context_checks=checks,evicted_results=evictions,saved_notes=list(notes))
            if on_event:on_event({'type':'phase','text':'Tool: '+str(name)})
            tool_started=time.perf_counter()
            if name=='search':result=searcher.search(**action) if searcher is not None else library.search(**action)
            elif name=='catalog':
                entries=library.catalog(**action);offset=int(action.get('offset',0))
                result={'results':entries,'offset':offset,'next_offset':offset+len(entries) if entries else None}
            elif name=='context':result=library.context(**action)
            elif name=='document':result=library.read(**action)
            elif name=='save_note':
                ident=int(action['evidence_id'])
                if ident not in exposed:raise ValueError('Evidence_id has not been delivered')
                proposed={**notes,ident:seen[ident]}
                # Token count includes JSON and a small framing allowance, using the actual GGUF tokenizer.
                if len(model.llm.tokenize(packed(proposed).encode('utf-8'),add_bos=False))>note_tokens:raise ValueError('Notebook full; remove a note or read a smaller passage')
                notes=proposed;result={'saved':ident}
            elif name=='remove_note':notes.pop(int(action['evidence_id']),None);result={'removed':True}
            elif name=='checkpoint':recent=[];result={'checkpoint':True}
            else:raise ValueError('Unknown action')
        except (ValueError,TypeError,KeyError,OverflowError) as error:
            result={'error':str(error)};name='invalid' if 'name' not in locals() else name
        except __import__('sqlite3').Error as error:result={'error':str(error)}
        finally:
            if tool_started is not None:
                elapsed=time.perf_counter()-tool_started
                key={'search':'fts_search_seconds','catalog':'catalog_seconds','context':'context_seconds','document':'document_seconds',
                     'save_note':'notes_seconds','remove_note':'notes_seconds','checkpoint':'notes_seconds'}.get(name,'other_tools_seconds')
                stages[key]+=elapsed
        serial+=1
        # A single large tool result is trimmed as data; the model can request smaller pages.
        entry={'evidence_id':serial,'tool':name,'submitted_action':submitted,'result':result}
        while model.count([{'role':'user','content':packed(entry)}])>min(2500,model.context//3):
            if isinstance(result,dict) and result.get('results'):
                result['results'].pop();result['truncated_for_context']=True
                result['next_offset']=int(action.get('offset',0))+len(result['results'])
            elif isinstance(result,dict) and isinstance(result.get('text'),str) and len(result['text'])>200:
                result['text']=result['text'][:len(result['text'])//2];result['end_char']=result.get('start_char',0)+len(result['text']);result['next_start']=result['end_char'];result['truncated_for_context']=True
            elif isinstance(result,list) and result:
                result.pop()
            else:result={'error':'Result metadata too large; request fewer items'};entry['result']=result;break
        seen[serial]=result;recent.append(entry);trace.append(entry)
        # Trace-only timing does not change evidence sent back to the model.
        trace[-1]=dict(entry,tool_seconds=elapsed if tool_started is not None else 0.0)
    return dict(status='round_limit',answer=None,trace=trace,context_checks=checks)
