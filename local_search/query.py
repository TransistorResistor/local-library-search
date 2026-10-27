"""Compile literal search input into FTS5, without repairing advanced queries."""

FIELDS={'all','any','exclude','near'}


def literal(value):
    if not isinstance(value,str) or not value.strip() or len(value)>256 or '\x00' in value:
        raise ValueError('Each literal must be nonempty text of at most 256 characters')
    if not any(c.isalnum() for c in value):raise ValueError('A literal must contain a word or number')
    return '"'+value.strip().replace('"','""')+'"'


def simple_terms(text):
    if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('Search must be nonempty text of at most 4000 characters')
    text=text.translate(str.maketrans({'“':'"','”':'"','‘':"'",'’':"'"}))
    terms=[];i=0
    while i<len(text):
        if text[i].isspace():i+=1;continue
        if text[i]=='"':
            i+=1;body=[]
            while i<len(text):
                if text[i]=='"':
                    if i+1<len(text) and text[i+1]=='"':body.append('"');i+=2;continue
                    i+=1;break
                body.append(text[i]);i+=1
            else:raise ValueError('Unmatched quote: close the phrase or remove the opening quote')
            if i<len(text) and not text[i].isspace():raise ValueError('Separate a quoted phrase from the next word with a space')
            terms.append(''.join(body))
        else:
            start=i
            while i<len(text) and not text[i].isspace():i+=1
            terms.append(text[start:i])
    return terms


def compile_query(query=None,input_mode='advanced',**fields):
    if input_mode not in ('simple','advanced'):raise ValueError('input_mode must be simple or advanced')
    unknown=set(fields)-FIELDS
    if unknown:raise ValueError('Unknown structured search fields: '+', '.join(sorted(unknown)))
    if fields and query is not None:raise ValueError('Use raw query or structured literals, not both')
    if fields:
        groups=[];negative=[];count=0
        for key in ('all','any','exclude'):
            if key not in fields:continue
            values=fields[key]
            if not isinstance(values,list) or not 1<=len(values)<=24:raise ValueError(key+' must contain 1..24 literals')
            count+=len(values);quoted=[literal(v) for v in values]
            if key=='exclude':negative=quoted
            elif key=='all':groups.extend(quoted)
            else:groups.append('('+' OR '.join(quoted)+')')
        if 'near' in fields:
            near=fields['near']
            if not isinstance(near,dict) or set(near)-{'terms','distance'}:raise ValueError('near accepts terms and distance only')
            terms=near.get('terms');distance=near.get('distance',10)
            if not isinstance(terms,list) or not 2<=len(terms)<=12:raise ValueError('near.terms must contain 2..12 literals')
            if type(distance) is not int or not 0<=distance<=100:raise ValueError('NEAR distance must be an integer from 0 to 100')
            count+=len(terms);groups.append('NEAR('+' '.join(literal(v) for v in terms)+', '+str(distance)+')')
        if count>48:raise ValueError('Use at most 48 literals per search')
        if not groups:raise ValueError('Include a positive all, any or near clause; exclusions alone cannot be searched')
        compiled=' AND '.join(groups)
        if negative:compiled='('+compiled+') NOT ('+' OR '.join(negative)+')'
    elif input_mode=='simple':
        terms=simple_terms(query)
        if len(terms)>48:raise ValueError('Use at most 48 words/phrases')
        compiled=' AND '.join(literal(term) for term in terms)
    else:
        if not isinstance(query,str) or not query.strip() or len(query)>4000 or '\x00' in query:
            raise ValueError('query must be a nonempty FTS5 string of at most 4000 characters')
        compiled=query
    if len(compiled)>4000:raise ValueError('Compiled query exceeds 4000 characters; use fewer literals')
    return compiled


def normalise_search(args):
    args=dict(args)
    fields={key:args.pop(key) for key in FIELDS if key in args}
    args['query']=compile_query(args.pop('query',None),args.pop('input_mode','advanced'),**fields)
    for key,value in {'date_field':'added_at','after':None,'before':None,'collection':None,'sort':'relevance','exact_count':True}.items():args.setdefault(key,value)
    return args
