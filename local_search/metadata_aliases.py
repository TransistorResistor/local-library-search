"""Deterministic whole-value aliases; quoted evidence is never rewritten."""
import copy
import unicodedata


def key(value):return ' '.join(unicodedata.normalize('NFKC',value).split()).casefold()


def compile_aliases(aliases):
    if not isinstance(aliases,dict) or len(aliases)>1000:raise ValueError('Aliases must map up to 1,000 source names to canonical names')
    mapping={}
    for alias,target in aliases.items():
        if any(not isinstance(value,str) or not value.strip() or len(value)>512 or '\x00' in value for value in (alias,target)):raise ValueError('Alias names must be nonempty text up to 512 characters')
        normal=key(alias)
        if normal in mapping and mapping[normal]!=target:raise ValueError('Conflicting alias spellings: '+alias)
        mapping[normal]=target
    resolved={}
    for alias,target in mapping.items():
        seen={alias}
        while key(target) in mapping and mapping[key(target)]!=target:
            current=key(target)
            if current in seen:raise ValueError('Alias cycle detected')
            seen.add(current);target=mapping[current]
        resolved[alias]=target
    canonical={}
    for target in resolved.values():
        normal=key(target)
        if normal in canonical and canonical[normal]!=target:raise ValueError('Conflicting preferred name spellings: '+target)
        canonical[normal]=target
    resolved.update(canonical)
    return resolved


def normalise_item(item,field):
    if not field.get('aliases') or not isinstance(item,dict):return copy.deepcopy(item)
    mapping=compile_aliases(field['aliases']);value=item.get('value');result=copy.deepcopy(item)
    if field['type']=='string' and isinstance(value,str):result['value']=mapping.get(key(value),value)
    elif field['type']=='string_list' and isinstance(value,list) and all(isinstance(member,str) for member in value):
        mapped=[mapping.get(key(member),member) for member in value]
        if mapped!=value:result['value']=list(dict.fromkeys(mapped))
    return result


def normalise(metadata,profile,skip_fields=()):
    result=copy.deepcopy(metadata);changes=[]
    if not isinstance(metadata,dict):return result,changes
    for field in profile['fields']:
        name=field['name']
        if name in skip_fields or name not in metadata:continue
        item=normalise_item(metadata[name],field)
        if item!=metadata[name]:
            changes.append({'field':name,'original':metadata[name]['value'],'canonical':item['value']});result[name]=item
    return result,changes
