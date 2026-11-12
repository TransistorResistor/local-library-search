"""Compact model-facing views; original evidence remains in the trace and UI."""

def compact(value):
    sources={}
    metadata={'title','collection','added_at','modified_at','published_at','date_precision'}
    def walk(item):
        if isinstance(item,list):return [walk(child) for child in item]
        if not isinstance(item,dict):return item
        omitted={'timings','rank','rerank_score','locator','detail','added_basis'}
        result={key:walk(child) for key,child in item.items() if key not in omitted and child is not None}
        for field,id_key in (('locations','source_id'),('sources','id')):
            if field not in result:continue
            references=[]
            for source in result[field]:
                ident=source.get(id_key)
                sources[ident]={'source_id':ident,**{key:child for key,child in source.items() if key in metadata}}
                references.append({key:child for key,child in source.items() if key in (id_key,'doc_id','page','start_char','end_char')})
            result[field]=references
        return result
    result=walk(value)
    if sources and isinstance(result,dict):result['source_metadata']=list(sources.values())
    return result


def leaves(result):
    if isinstance(result,dict) and 'batch' in result:
        for item in result['batch']:yield from leaves(item['result'])
    else:yield result
