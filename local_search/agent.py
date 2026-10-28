"""Small JSON tool loop; source excerpts persist as bounded notes across checkpoints."""
import json
import time
from .model import parse_action
from .prompts import load_prompt
import hashlib
from .policy import MODES,GUIDANCE as COMPLETION_GUIDANCE,coverage_update,source_ids,RetrievalProgress
from .query import normalise_search



def packed(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def ask(library,model,question,max_rounds=None,note_tokens=6000,on_event=None,prompts_file=None,reranker=None,effort='legacy',steering=None):
    if type(note_tokens) is not int or not 64<=note_tokens<=32768:
        raise ValueError('Notebook budget must be 64..32768 tokens')
    if not isinstance(effort,str) or effort not in MODES:raise ValueError('effort must be legacy, quick, balanced or thorough')
    max_rounds=MODES[effort] if max_rounds is None else max_rounds
    if type(max_rounds) is not int or not 1<=max_rounds<=100:raise ValueError('max_rounds must be 1..100')
    started=time.perf_counter()
    stages={'model_seconds':0.0,'fts_search_seconds':0.0,'catalog_seconds':0.0,'context_seconds':0.0,'document_seconds':0.0,'notes_seconds':0.0,'other_tools_seconds':0.0}
    system_prompt=load_prompt('search.system',prompts_file)
    if steering is not None:system_prompt+='\nThe user_steering list contains later instructions from the user for this question, in order. Apply them alongside the original question; later corrections take precedence. They persist through checkpoints. Reassess coverage after a correction and keep using retrieved evidence.'
    if effort!='legacy':system_prompt+='\n'+COMPLETION_GUIDANCE+'\nEffort: '+effort+'. '+{'quick':'Be direct and finish promptly once material needs are addressed.','balanced':'Resolve material ambiguity with targeted checks.','thorough':'Broaden coverage and check revisions, conflicts and independent sources where warranted.'}[effort]
    searcher=None
    if reranker is not None and reranker.config.enabled:
        from .rerank import QuestionSearch,GUIDANCE
        searcher=QuestionSearch(library,reranker,question,getattr(model,'cancel_event',None),on_event)
        system_prompt+='\n\n'+GUIDANCE
    owned=not library.db.in_transaction
    if owned:library.db.execute('BEGIN')
    try:
        result=_ask(library,model,question,max_rounds,note_tokens,on_event,system_prompt,stages,searcher,effort,steering)
        if hasattr(model,'diagnostics'):result['model_diagnostics']=model.diagnostics()
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
            if row:
                pages=set()
                for entry in result['trace']:
                    evidence=entry.get('result',{})
                    for hit in evidence.get('results',[]) if isinstance(evidence,dict) else []:
                        if isinstance(hit,dict):
                            pages.update(loc['page'] for loc in hit.get('locations',[]) if loc.get('source_id')==ident and type(loc.get('page')) is int)
                    if isinstance(evidence,dict) and type(evidence.get('page')) is int and any(s.get('id')==ident for s in evidence.get('sources',[])):
                        pages.add(evidence['page'])
                result['sources'].append(dict(row,page=next(iter(pages)) if len(pages)==1 else None,known_pages=sorted(pages)))
    finally:
        if steering is not None:steering.snapshot(close=True)
        if owned:library.db.rollback()
    total=time.perf_counter()-started
    stages['agent_processing_seconds']=max(0,total-sum(stages.values()))
    result['timings']={'total_seconds':total,'model_rounds':len(result['context_checks']),**stages}
    return result


def _ask(library,model,question,max_rounds,note_tokens,on_event,system_prompt,stages,searcher,effort,steering=None):
    recent=[];notes={};seen={};exposed=set();serial=0;trace=[];checks=[];evictions=0
    coverage=[];retrieval=RetrievalProgress();policy_events=[];finalising=False;instructions=[];steering_retry=False
    def notebook(saved=None):
        saved=notes if saved is None else saved
        used=len(model.llm.tokenize(packed(saved).encode('utf-8'),add_bos=False)) if saved else 0
        return {'used_tokens':used,'limit_tokens':note_tokens,'remaining_tokens':max(0,note_tokens-used),'items':len(saved)}
    def finish(**result):
        if steering is not None:
            items=steering.snapshot(close=True);result['steering']=items
            result['steering_extra_rounds']=max(0,len(checks)-max_rounds)
            if any(i['applied_round'] is None for i in items) and result['status']!='cancelled':
                result.update(status='steering_pending',stop_reason='steering_budget_exhausted',answer=None)
        result.setdefault('stop_reason',result['status'])
        return dict(result,notebook=notebook(),effort=effort,response_budget=max_rounds,coverage=coverage,policy_events=policy_events)
    def messages(saved=None,history=None,checklist=None):
        saved=notes if saved is None else saved
        history=recent if history is None else history
        checklist=coverage if checklist is None else checklist
        payload={'saved_evidence':saved,'notebook':notebook(saved),'recent_results':history,'evicted_results':evictions}
        if instructions:payload['user_steering']=[i['text'] for i in instructions]
        if effort!='legacy':
            already=set(saved)|{entry['evidence_id'] for entry in history}
            pinned={i:seen[i] for item in checklist for i in item['evidence_ids'] if i not in already}
            payload.update(coverage=checklist,coverage_evidence=pinned,unproductive_retrievals=retrieval.stalled)
            payload['instruction']='FINALISE NOW: only answer is allowed. Use visible evidence, disclose unknowns and unresolved needs.' if finalising else 'Address the next material evidence gap, or answer if ready.'
            if retrieval.stalled>=3:payload['stall_warning']='Recent retrieval added no new excerpts. Answer if ready; otherwise target a concrete gap with a different strategy. Do not treat repeated/empty results as proof of absence.'
        return [{'role':'system','content':system_prompt},{'role':'user','content':question},{'role':'user','content':packed(payload)}]
    for step in range(max_rounds+(2 if steering is not None else 0)):
        if step>=max_rounds and not steering_retry:break
        steering_retry=False
        finalising=effort!='legacy' and step>=max_rounds-1
        if steering is not None:
            applied=steering.apply(step+1);instructions.extend(applied)
            if applied:
                coverage=[];retrieval.stalled=0
                if searcher is not None:searcher.question=question+'\nUser instructions:\n'+'\n'.join(i['text'] for i in instructions)
                for item in applied:
                    trace.append({'round':step+1,'tool':'steering','status':'applied','instruction':item})
                    if on_event:on_event({'type':'steering','status':'applied',**item})
        if getattr(model,'cancel_event',None) is not None and model.cancel_event.is_set():
            return finish(status='cancelled',answer=None,trace=trace,context_checks=checks)
        prompt=messages()
        while model.count(prompt)+model.output>model.context and recent:
            recent.pop(0);evictions+=1;prompt=messages()
        if model.count(prompt)+model.output>model.context:
            if instructions:return finish(status='incomplete_output',stop_reason='steering_context_limit',answer=None,trace=trace,context_checks=checks)
            raise ValueError('Question and saved notes exceed context; shorten question or notebook budget')
        visible=set(notes)|{entry['evidence_id'] for entry in recent}
        if effort!='legacy':visible.update(i for item in coverage for i in item['evidence_ids'])
        exposed.update(entry['evidence_id'] for entry in recent)
        exposed.update(notes)
        if on_event:on_event({'type':'round','round':step+1,'thinking':getattr(model,'thinking',False)})
        if finalising:
            policy_events.append({'round':step+1,'reason':'reserved_final_response'})
            if on_event:on_event({'type':'phase','text':'Finalising answer from retrieved evidence'})
        model_started=time.perf_counter()
        response=model.complete(prompt,on_event=on_event) if on_event else model.complete(prompt)
        stages['model_seconds']+=time.perf_counter()-model_started
        checks.append({'reserved_total':response['reserved_total'],'usage':response.get('usage',{}),'timings':response.get('timings',{}),'finish_reason':response['choices'][0].get('finish_reason')})
        if getattr(model,'cancel_event',None) is not None and model.cancel_event.is_set():
            return finish(status='cancelled',answer=None,trace=trace,context_checks=checks)
        if response['choices'][0].get('finish_reason')=='length':
            trace.append({'round':step+1,'tool':'invalid','status':'rejected','submitted_action':None,'model_response':response['choices'][0]['text'],
                          'rejection':{'stage':'generation','error_type':'OutputLimit','reason':'Model response reached its output limit'},'result':{'error':'Output limit reached'}})
            return finish(status='output_budget_exhausted' if effort=='legacy' else 'incomplete_output',stop_reason='output_budget_exhausted',answer=None,trace=trace,context_checks=checks)
        name='invalid';action={};submitted=None;tool_started=None;rejection=None;stage='parse'
        try:
            action=parse_action(response['choices'][0]['text']);submitted=dict(action)
            candidate=action.pop('action')
            if not isinstance(candidate,str):raise ValueError('action must be text')
            name=candidate
            stage='validation'
            if effort!='legacy' and 'coverage' in action:
                proposed_coverage=coverage_update(action.pop('coverage'),exposed)
                for item in proposed_coverage:
                    if item['status'] in ('supported','conflicting') and any(not source_ids(seen[i]) for i in item['evidence_ids']):
                        raise ValueError('Supported/conflicting coverage references must contain retrieved source evidence, not tool errors or acknowledgements')
                if model.count([{'role':'user','content':packed(proposed_coverage)}])>min(1000,model.context//8):raise ValueError('Coverage checklist too large; shorten the needs')
                if model.count(messages(history=[],checklist=proposed_coverage))+model.output+256>model.context:raise ValueError('Checklist evidence exceeds context; reference fewer/smaller evidence items')
                if proposed_coverage!=coverage:retrieval.stalled=0
                coverage=proposed_coverage
                if on_event:on_event({'type':'coverage','items':coverage})
            if finalising and name!='answer':raise ValueError('Final response must be answer; no further tools are allowed')
            if name=='answer':
                citations=action.get('citations',[])
                allowed=set()
                for ident in exposed if effort=='legacy' else visible:allowed.update(source_ids(seen[ident]))
                if not isinstance(citations,list) or any(type(i) is not int or i not in allowed for i in citations):
                    raise ValueError('Invalid citations '+packed(citations)+'. Use source IDs from retrieved evidence: '+packed(sorted(allowed)[:40])+'. Do not cite evidence_id, passage_id, or reference numbers inside source text.')
                if not isinstance(action.get('answer'),str):raise ValueError('answer must be text')
                gaps=action.get('gaps',[])
                if not isinstance(gaps,list) or len(gaps)>12 or any(not isinstance(g,str) or len(g)>512 for g in gaps):raise ValueError('gaps must contain at most 12 short text entries')
                if effort!='legacy' and not finalising and (not coverage or any(item['status']=='needed' for item in coverage)):
                    raise ValueError('Provide coverage for requested parts; mark remaining needs unresolved with honest gaps before answering')
                if effort!='legacy' and any(item['status']=='supported' for item in coverage) and not citations:
                    raise ValueError('Cite the retrieved source IDs supporting the answered needs')
                incomplete=effort!='legacy' and (not coverage or bool(gaps) or any(item['status']!='supported' for item in coverage))
                if incomplete:
                    gaps=list(dict.fromkeys(gaps+[item['need'] for item in coverage if item['status']!='supported']))
                    if not coverage:gaps.append('Requested-part coverage was not recorded; this answer may be incomplete.')
                reason='budget_exhausted' if finalising else 'answered_with_gaps' if incomplete else 'evidence_complete' if effort!='legacy' else 'model_answer'
                if steering is not None and not steering.finalise():
                    trace.append({'round':step+1,'tool':'answer','status':'superseded','reason':'queued_steering','submitted_action':submitted})
                    steering_retry=True
                    if on_event:on_event({'type':'phase','text':'New instruction queued; revising before completing'})
                    continue
                return finish(status='answered_with_gaps' if incomplete else 'answered',stop_reason=reason,answer=action['answer'],gaps=gaps,citations=citations,trace=trace,context_checks=checks,evicted_results=evictions,saved_notes=list(notes))
            if on_event:on_event({'type':'phase','text':'Tool: '+str(name)})
            tool_started=time.perf_counter()
            stage='tool'
            if name=='search':
                action=normalise_search(action)
                result=searcher.search(**action) if searcher is not None else library.search(**action)
            elif name=='catalog':
                entries=library.catalog(**action);offset=int(action.get('offset',0))
                result={'results':[dict(row,source_id=row['id']) for row in entries],'offset':offset,'next_offset':offset+len(entries) if entries else None}
            elif name=='context':result=library.context(**action)
            elif name=='document':result=library.read(**action)
            elif name=='save_note':
                ident=int(action['evidence_id'])
                if ident not in exposed:raise ValueError('Evidence_id has not been delivered')
                proposed={**notes,ident:seen[ident]}
                proposed_usage=notebook(proposed);current_usage=notebook()
                if proposed_usage['used_tokens']>note_tokens:
                    raise ValueError(f"Evidence {ident} will not fit: notebook uses {current_usage['used_tokens']}/{note_tokens} tokens; "
                                     f"this save would use {proposed_usage['used_tokens']} ({proposed_usage['used_tokens']-note_tokens} over budget). "
                                     'Nothing was saved. Remove a note or read a smaller passage and save its evidence ID.')
                if model.count(messages(saved=proposed,history=[]))+model.output>model.context:
                    raise ValueError('Evidence will not fit the total model context with the question and output reserve. Nothing was saved; remove a note or read a smaller passage.')
                notes=proposed;result={'saved':ident,'notebook':notebook()}
            elif name=='remove_note':notes.pop(int(action['evidence_id']),None);result={'removed':True,'notebook':notebook()}
            elif name=='checkpoint':recent=[];result={'checkpoint':True,'notebook':notebook()}
            else:raise ValueError('Unknown action')
        except (ValueError,TypeError,KeyError,OverflowError) as error:
            result={'error':str(error)}
            rejection={'stage':stage,'error_type':type(error).__name__,'reason':str(error)}
        except __import__('sqlite3').Error as error:
            result={'error':str(error)}
            rejection={'stage':stage,'error_type':type(error).__name__,'reason':str(error)}
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
        if effort!='legacy' and name in ('search','catalog','context','document'):
            novel=retrieval.observe(name,result)
            trace[-1]=dict(trace[-1],new_evidence=novel,retrieval_key=packed([name,action]))
            if retrieval.stalled==3:
                policy_events.append({'round':step+1,'reason':'retrieval_stall','consecutive_unproductive':retrieval.stalled})
                if on_event:on_event({'type':'phase','text':'Repeated retrieval added no new evidence; reviewing remaining gaps'})
        # Trace-only diagnostics preserve the existing model feedback/context budget.
        trace[-1]=dict(trace[-1],round=step+1,status='rejected' if rejection else 'completed',
                       notebook=notebook(),tool_seconds=elapsed if tool_started is not None else 0.0)
        if rejection:
            trace[-1]['rejection']=rejection
            if submitted is None:trace[-1]['model_response']=response['choices'][0]['text']
        if effort!='legacy':trace[-1].update(coverage=coverage,unproductive_retrievals=retrieval.stalled,finalising=finalising)
        if on_event and name in ('save_note','remove_note','checkpoint'):
            on_event({'type':'notebook',**notebook(),'error':rejection['reason'] if rejection else None})
    return finish(status='round_limit' if effort=='legacy' else 'incomplete_output',stop_reason='round_limit' if effort=='legacy' else 'budget_exhausted',answer=None,trace=trace,context_checks=checks)
