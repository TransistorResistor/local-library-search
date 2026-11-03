"""Previewed bulk review actions, with per-finding concurrency and validation."""
import copy
import json
import re
import sqlite3
from . import metadata_review as review
from .metadata_profile import canonical

REVIEWABLE={'needs_review','extracted','unknown'}


def field_names(path):
    with review.connection(path) as db:
        return sorted({field['name'] for row in db.execute('SELECT definition FROM metadata_profiles') for field in json.loads(row[0])['fields']})


def _targets(db,ids,filters):
    if ids is not None:
        yield from dict.fromkeys(ids);return
    status=filters.get('status','needs_review');query=filters.get('query','');collection=filters.get('collection','');profile=filters.get('profile','')
    yield from (row[0] for row in db.execute('''SELECT j.id FROM metadata_jobs j
        LEFT JOIN sources s ON s.source_uuid=j.source_uuid JOIN metadata_profiles p ON p.hash=j.profile_hash
        WHERE (?='all' OR j.status=?) AND (?='' OR instr(lower(coalesce(s.title,'')),lower(?))>0)
        AND (?='' OR s.collection=?) AND (?='' OR instr(lower(p.name),lower(?))>0)
        ORDER BY j.created_at,j.rowid''',(status,status,query,query,collection,collection,profile,profile)))


def replace_value(item,find,replacement,case_sensitive=False,whole=False):
    """Literal replacement in values/scopes, never evidence or precision labels."""
    pattern=re.compile((r'\A'+re.escape(find)+r'\Z') if whole else re.escape(find),0 if case_sensitive else re.IGNORECASE)
    matches=0
    def walk(value):
        nonlocal matches
        if isinstance(value,str):
            changed,count=pattern.subn(lambda match:replacement,value);matches+=count;return changed
        if isinstance(value,list):return [walk(member) for member in value]
        if isinstance(value,dict):return {key:walk(member) if key in ('value','scope','start','end') else copy.deepcopy(member) for key,member in value.items()}
        if type(value) in (int,float,bool):
            text=canonical(value)
            if (text==find if case_sensitive else text.casefold()==find.casefold()):
                matches+=1
                return replacement.strip().lower()=='true' if type(value) is bool and replacement.strip().lower() in ('true','false') else json.loads(replacement)
        return value
    if not isinstance(item,dict) or 'value' not in item:return item,0
    result=copy.deepcopy(item);result['value']=walk(item['value'])
    if isinstance(item.get('scope'),str):result['scope']=walk(item['scope'])
    if result==item:return result,0
    if matches:result['evidence']=[]
    return result,matches


def preview(path,operation,ids=None,filters=None,field=None,find='',replacement='',case_sensitive=False,whole=False,cancel=None,on_progress=None):
    if operation not in ('approve','replace'):raise ValueError('Unknown bulk operation')
    if operation=='replace' and (not isinstance(find,str) or not 1<=len(find)<=1000 or not isinstance(replacement,str) or len(replacement)>4000 or not field):raise ValueError('Choose a field and enter 1–1,000 characters to find; replacement can be empty or up to 4,000 characters.')
    plan={'operation':operation,'entries':[],'skipped':[],'target_count':0,'cancelled':False,'field':field,'find':find,'replacement':replacement,'case_sensitive':case_sensitive,'whole':whole}
    with review.connection(path) as db:
        db.execute('BEGIN')
        for ident in _targets(db,ids,filters or {}):
            if cancel is not None and cancel.is_set():plan['cancelled']=True;break
            plan['target_count']+=1
            if plan['target_count']>50000:raise ValueError('Narrow the filters to at most 50,000 findings for a bulk action.')
            title=str(ident)
            try:
                record=review._fetch(db,ident);title=record['title'];status=record['job']['status']
                if operation=='approve' and status not in REVIEWABLE:raise ValueError('Already accepted or not ready for review: '+status)
                if operation=='replace' and status not in REVIEWABLE|{'accepted'}:raise ValueError('Not ready for editing: '+status)
                review.check_source(db,record)
                metadata=copy.deepcopy(record['metadata']);manual=set(record['manual_fields']);preserve=set(record['preserve_evidence_fields']);count=0
                before=after=''
                if operation=='replace':
                    if not isinstance(metadata,dict) or field not in metadata:raise ValueError('Field is not present in this finding')
                    before=canonical(metadata[field]);changed,count=replace_value(metadata[field],find,replacement,case_sensitive,whole)
                    if not count:raise ValueError('No matching value')
                    metadata[field]=changed;manual.add(field);preserve.discard(field);after=canonical(changed)
                parsed,issues,manual,preserve=review.prepare(record,metadata,manual,preserve)
                blocking=issues if operation=='approve' else [issue for issue in issues if issue.startswith(field+':')]
                if blocking:raise ValueError('; '.join(blocking))
                plan['entries'].append({'id':ident,'token':record['review_token'],'title':title,'matches':count,'before':before[:1200],'after':after[:1200],'other_issues':issues})
            except (ValueError,TypeError,KeyError) as error:plan['skipped'].append({'id':ident,'title':title,'reason':str(error)})
            if on_progress and plan['target_count']%25==0:on_progress(plan['target_count'])
    return plan


def apply(path,plan,cancel=None,on_progress=None):
    if plan.get('cancelled'):raise ValueError('Preview was cancelled; prepare a complete preview first')
    operation=plan.get('operation')
    if operation not in ('approve','replace'):raise ValueError('Unknown bulk operation')
    result={'applied':[],'skipped':list(plan['skipped']),'cancelled':False,'remaining':0}
    for index,entry in enumerate(plan['entries']):
        if cancel is not None and cancel.is_set():result.update(cancelled=True,remaining=len(plan['entries'])-index);break
        try:
            record=review.fetch(path,entry['id'])
            if record['review_token']!=entry['token']:raise ValueError('Finding changed since preview; prepare a new preview')
            metadata=copy.deepcopy(record['metadata']);manual=set(record['manual_fields']);preserve=set(record['preserve_evidence_fields'])
            if operation=='replace':
                field=plan['field'];metadata[field],count=replace_value(metadata[field],plan['find'],plan['replacement'],plan['case_sensitive'],plan['whole'])
                if not count:raise ValueError('Value no longer matches the preview')
                manual.add(field);preserve.discard(field)
            review.save(path,entry['id'],metadata,entry['token'],manual,operation=='approve',preserve)
            result['applied'].append({'id':entry['id'],'title':entry['title']})
        except (ValueError,TypeError,KeyError,sqlite3.Error) as error:result['skipped'].append({'id':entry['id'],'title':entry['title'],'reason':str(error)})
        if on_progress:on_progress(index+1)
    return result
