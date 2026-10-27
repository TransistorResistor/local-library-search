"""Non-overlapping wall-clock stages for a completed desktop question."""


def complete_timing(result,clicked,submitted,worker_started,worker_finished,render_started,completed,load_seconds,library_seconds):
    agent=result['timings'];checks=result.get('context_checks',[])
    prep=sum(c.get('timings',{}).get('preparation_seconds',0) for c in checks)
    inference=sum(c.get('timings',{}).get('inference_seconds',0) for c in checks)
    stages={'Settings / UI setup':submitted-clicked,'Worker queue':worker_started-submitted,
            'Model load / reuse':load_seconds,'Database connection':library_seconds,
            'Model prompt formatting':prep,'Model inference':inference,
            'Model response processing':max(0,agent['model_seconds']-prep-inference)}
    for label,key in [('FTS searches','fts_search_seconds'),('Reranking / shortlist work','rerank_seconds'),('Catalogue lookups','catalog_seconds'),
                      ('Context expansion','context_seconds'),('Document reads','document_seconds'),
                      ('Notebook operations','notes_seconds'),('Other tools','other_tools_seconds'),
                      ('Context budgeting / validation / source resolution','agent_processing_seconds')]:
        stages[label]=agent.get(key,0)
    stages['Worker bookkeeping / initial trace save']=max(0,worker_finished-worker_started-load_seconds-library_seconds-agent['total_seconds'])
    stages['UI delivery / polling']=render_started-worker_finished
    stages['Final answer rendering']=completed-render_started
    first=next((c.get('timings',{}).get('first_output_seconds') for c in checks if c.get('timings',{}).get('first_output_seconds') is not None),None)
    return {'click_to_complete_seconds':completed-clicked,'stages_seconds':stages,
            'first_generation_output_seconds':first,'status':result['status'],
            'model_rounds':agent['model_rounds']}


def format_timing(timing):
    label='Click → final answer complete' if timing['status'] in ('answered','answered_with_gaps') else 'Click → stopped ('+timing['status']+')'
    text=f"{label}: {timing['click_to_complete_seconds']:.3f}s\n"
    text+='\n'.join(f'{name}: {seconds:.3f}s' for name,seconds in timing['stages_seconds'].items())
    text+=f"\nModel rounds: {timing['model_rounds']}"
    if timing['first_generation_output_seconds'] is not None:
        text+=f"\nFirst generation request → first output: {timing['first_generation_output_seconds']:.3f}s (included above)"
    return text
