"""Private, atomic local conversation storage; never stored in the library snapshot."""
import json
import os
import time
import uuid
from pathlib import Path


class ConversationStore:
    def __init__(self,config_path):
        import hashlib
        key=hashlib.sha256(str(Path(config_path).resolve()).encode()).hexdigest()[:20]
        self.directory=Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'LocalLibrarySearch/conversations'/key

    def save(self,payload,ident=None):
        ident=ident or uuid.uuid4().hex
        if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid conversation identity')
        self.directory.mkdir(parents=True,exist_ok=True)
        value={**payload,'id':ident,'updated_at':time.time(),'version':1}
        target=self.directory/(ident+'.json');stage=target.with_suffix('.tmp')
        stage.write_text(json.dumps(value,ensure_ascii=False),'utf-8');stage.replace(target)
        summary=target.with_suffix('.summary');stage=summary.with_suffix('.summary.tmp')
        stage.write_text(json.dumps({key:value.get(key) for key in ('id','title','library','updated_at')},ensure_ascii=False),'utf-8');stage.replace(summary);return ident

    def listing(self):
        if not self.directory.exists():return []
        items=[]
        for path in self.directory.glob('*.json'):
            try:
                summary=path.with_suffix('.summary');value=json.loads((summary if summary.exists() else path).read_text('utf-8'));items.append({k:value.get(k) for k in ('id','title','library','updated_at')})
            except (OSError,ValueError):continue
        return sorted(items,key=lambda item:item.get('updated_at') or 0,reverse=True)

    def load(self,ident):
        if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid conversation identity')
        value=json.loads((self.directory/(ident+'.json')).read_text('utf-8'))
        if value.get('version')!=1 or not isinstance(value.get('transcript'),list):raise ValueError('Unsupported saved conversation')
        if not isinstance(value.get('conversation',[]),list) or not isinstance(value.get('turns',[]),list):raise ValueError('Invalid conversation history')
        for item in value['transcript']:
            if not isinstance(item,dict) or not isinstance(item.get('speaker'),str) or not isinstance(item.get('text'),str) or not isinstance(item.get('sources',[]),list):raise ValueError('Invalid saved transcript')
            for source in item.get('sources',[]):
                if not isinstance(source,dict) or type(source.get('id')) is not int or not isinstance(source.get('title'),str) or not isinstance(source.get('locator'),str):raise ValueError('Invalid saved source')
        return value


def export_text(transcript):
    parts=[]
    for item in transcript:
        parts.append(item['speaker']+'\n'+item['text'])
        for source in item.get('sources',[]):parts.append(f"[{source['id']}] {source['title']} — {source['locator']}")
    return '\n\n'.join(parts)+'\n'
