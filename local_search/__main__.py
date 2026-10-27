import argparse
import json
import sys
import time
from pathlib import Path
from .store import Library
from .ingest import refresh


def configuration(path):
    path=Path(path).resolve();data=json.loads(path.read_text('utf-8'))
    if not isinstance(data.get('sources'),list):raise ValueError('Configuration requires a sources list')
    names=[]
    for source in data['sources']:
        if source.get('type') not in ('folder','clipper'):raise ValueError('Source type must be folder or clipper')
        if not source.get('name') or not source.get('path'):raise ValueError('Every source requires name and path')
        if type(source.get('closed_snapshot',False)) is not bool:raise ValueError('closed_snapshot must be true or false')
        if source.get('network_mode') not in (None,'closed_snapshot','export'):raise ValueError('network_mode must be closed_snapshot or export')
        names.append(source['name'])
        source['path']=str((path.parent/Path(source['path']).expanduser()).resolve())
        if source.get('ascii_ratio',.8)<0 or source.get('ascii_ratio',.8)>1:raise ValueError('ascii_ratio must be 0..1')
    if len(set(names))!=len(names):raise ValueError('Source names must be unique and stable')
    data['database']=str((path.parent/Path(data.get('database','data/library.sqlite3'))).resolve())
    if data.get('prompts_file'):
        data['prompts_file']=str((path.parent/Path(data['prompts_file']).expanduser()).resolve())
    for source in data['sources']:
        if Path(source['path'])==Path(data['database']):raise ValueError('Library database must not overwrite a source database')
    model=data.setdefault('model',{})
    note_tokens=data.get('agent_options',{}).get('note_tokens',6000)
    if type(note_tokens) is not int or not 64<=note_tokens<=32768:raise ValueError('Notebook budget must be 64..32768 tokens')
    if data.get('agent_options',{}).get('effort','legacy') not in ('legacy','quick','balanced','thorough'):raise ValueError('Invalid answer effort mode')
    if data.get('search_input_mode','simple') not in ('simple','advanced'):raise ValueError('Invalid search input mode')
    if data.get('reranker',{}).get('model_dir'):
        data['reranker']['model_dir']=str((path.parent/Path(data['reranker']['model_dir']).expanduser()).resolve())
    for key in ('path','torch_lib'):
        if model.get(key):model[key]=str((path.parent/Path(model[key]).expanduser()).resolve())
    return data


def main():
    if sys.stdout is not None:sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description='Incremental clipper/PDF library with FTS5 and direct GGUF search.')
    p.add_argument('--config',default='local-search.json')
    sub=p.add_subparsers(dest='command',required=True)
    ingest=sub.add_parser('ingest');ingest.add_argument('--watch',type=int,metavar='SECONDS')
    sub.add_parser('status')
    cat=sub.add_parser('catalog');cat.add_argument('--offset',type=int,default=0);cat.add_argument('--limit',type=int,default=20);cat.add_argument('--status');cat.add_argument('--title-query')
    cat.add_argument('--input-mode',choices=('simple','advanced'),default='advanced')
    search=sub.add_parser('search');search.add_argument('query');search.add_argument('--limit',type=int,default=5);search.add_argument('--offset',type=int,default=0)
    search.add_argument('--after');search.add_argument('--before');search.add_argument('--date-field',choices=('added_at','modified_at','published_at'),default='added_at');search.add_argument('--sort',choices=('relevance','newest'),default='relevance');search.add_argument('--collection');search.add_argument('--no-count',action='store_true',help='Skip the exact total; retain pagination')
    search.add_argument('--input-mode',choices=('simple','advanced'),default='advanced',help='Simple quotes literal words/phrases; advanced preserves raw FTS5 (CLI default)')
    doc=sub.add_parser('document');doc.add_argument('doc_id',type=int);doc.add_argument('--start-char',type=int,default=0);doc.add_argument('--count-chars',type=int,default=4000)
    context=sub.add_parser('context');context.add_argument('passage_id',type=int);context.add_argument('--doc-id',type=int);context.add_argument('--radius-chars',type=int,default=1000)
    ask=sub.add_parser('ask');ask.add_argument('question');ask.add_argument('--trace',type=Path);ask.add_argument('--max-rounds',type=int,help='Override response budget, including finalisation')
    ask.add_argument('--effort',choices=('legacy','quick','balanced','thorough'),help='Override configured effort (6/12/24 responses; legacy retains previous policy)')
    ask.add_argument('--note-tokens',type=int,help='Notebook token limit (64..32768); overrides agent_options.note_tokens, default 6000')
    sub.add_parser('model-check')
    sub.add_parser('gui')
    args=p.parse_args()
    if args.command=='gui':
        from .gui import launch
        launch(args.config);return
    config=configuration(args.config)
    if args.command=='ingest' and args.watch is not None and args.watch<10:p.error('--watch must be at least 10 seconds')
    if args.command=='ingest':
        from .progress import terminal
        terminal('Opening ingestion library: '+config['database'])
    with Library(config['database'],**config.get('database_options',{})) as library:
        if args.command=='ingest':
            from .progress import TerminalProgress
            while True:
                result=refresh(library,config,on_progress=TerminalProgress());print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
                if not args.watch:
                    if any('error' in r or r.get('errors') or r.get('scan_errors') for r in result):raise SystemExit(1)
                    return
                time.sleep(args.watch)
        elif args.command=='status':result=library.status()
        elif args.command=='catalog':result=library.catalog(args.offset,args.limit,args.status,args.title_query,args.input_mode)
        elif args.command=='search':result=library.search(args.query,args.limit,args.offset,args.date_field,args.after,args.before,args.collection,args.sort,exact_count=not args.no_count,input_mode=args.input_mode)
        elif args.command=='document':result=library.read(args.doc_id,args.start_char,args.count_chars)
        elif args.command=='context':result=library.context(args.passage_id,args.doc_id,args.radius_chars)
        else:
            from .model import GGUF
            model=GGUF(**config['model'])
            try:
                if args.command=='model-check':
                    result=model.complete([{'role':'user','content':'Reply with the single word READY.'}])
                    result['dll_directories']=model.dlls.paths
                else:
                    from .agent import ask as ask_library
                    from .rerank import RerankConfig,Reranker
                    if args.max_rounds is not None and not 1<=args.max_rounds<=100:raise ValueError('max-rounds must be 1..100')
                    rerank_config=RerankConfig(**config.get('reranker',{}))
                    reranker=Reranker(rerank_config) if rerank_config.enabled else None
                    try:result=ask_library(library,model,args.question,max_rounds=args.max_rounds,prompts_file=config.get('prompts_file'),reranker=reranker,note_tokens=args.note_tokens if args.note_tokens is not None else config.get('agent_options',{}).get('note_tokens',6000),effort=args.effort or config.get('agent_options',{}).get('effort','legacy'))
                    finally:
                        if reranker:reranker.close()
                    if args.trace:
                        args.trace.parent.mkdir(parents=True,exist_ok=True);args.trace.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
                    result={k:v for k,v in result.items() if k not in ('trace','context_checks')}
            finally:model.close()
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as error:
        print(f'{type(error).__name__}: {error}',file=sys.stderr);raise SystemExit(1)
