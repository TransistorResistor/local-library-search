"""Optional bounded cross-encoder scoring, isolated from GGUF inference."""
import copy
from dataclasses import dataclass,asdict
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import time


@dataclass(frozen=True)
class RerankConfig:
    enabled:bool=False
    model_dir:str=''
    device:str='cpu'
    candidates:int=40
    top_k:int=5
    batch_size:int=8
    threads:int=2
    max_pair_tokens:int=512
    timeout_seconds:float=5
    startup_seconds:float=60

    def __post_init__(self):
        if type(self.enabled) is not bool:raise ValueError('Reranking enabled must be true/false')
        if not isinstance(self.model_dir,str):raise ValueError('Reranker model_dir must be a local folder path')
        if not isinstance(self.device,str) or (self.device!='cpu' and not re.fullmatch(r'cuda:[0-9]+',self.device)):raise ValueError('Reranker device must be cpu or cuda:N')
        for name,low,high in [('candidates',1,1000),('top_k',1,20),('batch_size',1,64),('threads',1,32),('max_pair_tokens',64,8192)]:
            if type(getattr(self,name)) is not int or not low<=getattr(self,name)<=high:raise ValueError(f'Reranker {name} must be {low}..{high}')
        for name,high in [('timeout_seconds',120),('startup_seconds',300)]:
            if not isinstance(getattr(self,name),(int,float)) or not math.isfinite(getattr(self,name)) or not 0<getattr(self,name)<=high:raise ValueError(f'Reranker {name} must be >0 and <= {high}')


def scoring_worker(connection,options):
    config=RerankConfig(**options)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false';os.environ['HF_HUB_DISABLE_PROGRESS_BARS']='1'
    os.environ['OMP_NUM_THREADS']=str(config.threads);os.environ['MKL_NUM_THREADS']=str(config.threads)
    if config.device=='cpu':os.environ['CUDA_VISIBLE_DEVICES']=''
    try:
        import torch
        torch.set_num_threads(config.threads);torch.set_num_interop_threads(1)
        if config.device.startswith('cuda:') and (not torch.cuda.is_available() or int(config.device.split(':')[1])>=torch.cuda.device_count()):
            raise ValueError('Requested CUDA device is unavailable in installed PyTorch')
        from sentence_transformers import CrossEncoder
        model=CrossEncoder(config.model_dir,device=config.device,local_files_only=True,trust_remote_code=False,max_length=config.max_pair_tokens)
        underlying=model if hasattr(model,'parameters') else model.model
        expected=torch.device(config.device)
        if any(p.device!=expected for p in underlying.parameters()):raise ValueError('Reranker device verification failed')
        if config.device=='cpu' and torch.cuda.is_initialized():raise ValueError('CPU reranker unexpectedly initialized CUDA')
        capacity=getattr(getattr(model,'config',None),'max_position_embeddings',config.max_pair_tokens)
        if config.max_pair_tokens>capacity:raise ValueError('Pair token budget exceeds model capacity')
        model.predict([('warmup','warmup')],batch_size=1,show_progress_bar=False)
        connection.send({'ready':True,'device':config.device})
        while True:
            pairs=connection.recv()
            if pairs is None:break
            counts=[len(model.tokenizer(a,b,truncation=False)['input_ids']) for a,b in pairs]
            if any(n>config.max_pair_tokens for n in counts):
                connection.send({'error':'pair_token_budget','pair_tokens':counts});continue
            with torch.inference_mode():values=model.predict(pairs,batch_size=config.batch_size,show_progress_bar=False,convert_to_numpy=True)
            scores=values.reshape(-1).tolist() if values.ndim==1 or (values.ndim==2 and values.shape[1]==1) else []
            connection.send({'scores':scores,'pair_tokens':counts,'device':config.device,'gpu_initialized':torch.cuda.is_initialized()})
    except Exception as error:
        try:connection.send({'error':'worker_failure','detail':type(error).__name__+': '+str(error)[:500]})
        except (EOFError,OSError):pass
    finally:connection.close()


class Reranker:
    def __init__(self,config,target=scoring_worker):
        self.config=config;self.target=target;self.process=None;self.connection=None;self.failure=None

    def close(self):
        if self.process is not None:
            if self.process.is_alive():self.process.terminate()
            if self.process.pid is not None:self.process.join(timeout=2)
            if self.process.is_alive():self.process.kill();self.process.join(timeout=2)
            self.process.close();self.process=None
        if self.connection is not None:self.connection.close();self.connection=None

    def receive(self,seconds,cancel_event):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if cancel_event is not None and cancel_event.is_set():
                self.close();return {'error':'cancelled'}
            if self.connection.poll(min(.05,max(0,end-time.monotonic()))):
                try:return self.connection.recv()
                except (EOFError,OSError):break
        else:
            self.failure='timeout';self.close();return {'error':'timeout'}
        self.failure='worker_disconnected';self.close();return {'error':self.failure}

    def score(self,pairs,cancel_event=None):
        started=time.perf_counter();cold=self.process is None
        if self.failure:return {'error':self.failure,'seconds':0,'cold':False}
        if not self.config.model_dir or not Path(self.config.model_dir).is_dir():
            self.failure='model_directory_missing';return {'error':self.failure,'seconds':0,'cold':True}
        load_seconds=0
        try:
            if cold:
                context=multiprocessing.get_context('spawn');self.connection,child=context.Pipe()
                self.process=context.Process(target=self.target,args=(child,asdict(self.config)),daemon=True)
                self.process.start();child.close()
                ready=self.receive(self.config.startup_seconds,cancel_event)
                load_seconds=time.perf_counter()-started
                if not ready.get('ready'):
                    reason=ready.get('error','startup_failure')
                    self.failure=None if reason=='cancelled' else reason;self.close()
                    return dict(ready,seconds=time.perf_counter()-started,load_seconds=load_seconds,cold=True)
            self.connection.send(pairs);result=self.receive(self.config.timeout_seconds,cancel_event)
            if result.get('error')=='worker_failure':self.failure='worker_failure';self.close()
            return dict(result,seconds=time.perf_counter()-started,load_seconds=load_seconds,cold=cold)
        except (OSError,EOFError,ValueError) as error:
            self.failure='worker_failure';self.close()
            return {'error':self.failure,'detail':str(error)[:500],'seconds':time.perf_counter()-started,'cold':cold}


GUIDANCE='''Optional reranking is enabled for search. FTS5 still selects matching passages.
search_scope defaults to "shortlist": pagination then covers only the bounded candidate pool,
not all lexical matches. Use search_scope:"exhaustive" to enumerate all matches without
reranking. Newest-first also bypasses reranking. relevance_query optionally supplies a
natural-language relevance question; otherwise the original user question is used.
Scores measure estimated relevance, not factual correctness, freshness or probability.
'''


class QuestionSearch:
    def __init__(self,library,reranker,question,cancel_event=None,on_event=None):
        self.library=library;self.reranker=reranker;self.question=question
        self.cancel_event=cancel_event;self.on_event=on_event;self.pools={};self.trace=[]
        self.fts_seconds=0;self.rerank_seconds=0

    def lexical(self,args):
        started=time.perf_counter()
        try:return self.library.search(**args)
        finally:self.fts_seconds+=time.perf_counter()-started

    def search(self,**args):
        args=dict(args);scope=args.pop('search_scope','shortlist');relevance=args.pop('relevance_query',self.question)
        if scope not in ('shortlist','exhaustive'):raise ValueError('search_scope must be shortlist or exhaustive')
        if not isinstance(relevance,str) or not relevance.strip() or len(relevance)>8000:raise ValueError('relevance_query must be nonempty text of at most 8000 characters')
        if scope=='exhaustive' or args.get('sort')=='newest':
            result=self.lexical(args);result['rerank']={'applied':False,'reason':'exhaustive_or_newest_bypass'};return result
        limit=min(self.reranker.config.top_k,max(1,min(20,int(args.pop('limit',self.reranker.config.top_k)))))
        offset=max(0,int(args.pop('offset',0)))
        binding=json.dumps([args,relevance],sort_keys=True,ensure_ascii=False)
        if offset and binding not in self.pools:raise ValueError('Shortlist expired or not yet created. Restart at offset 0, or use search_scope exhaustive.')
        started=time.perf_counter();before_fts=self.fts_seconds
        try:
            if binding not in self.pools:
                candidates=[];next_offset=0;first=None
                while len(candidates)<self.reranker.config.candidates:
                    if self.cancel_event is not None and self.cancel_event.is_set():raise ValueError('Search cancelled')
                    page_args=dict(args,limit=min(20,self.reranker.config.candidates-len(candidates)),offset=next_offset)
                    if first is not None:page_args['exact_count']=False
                    page=self.lexical(page_args)
                    if first is None:first=page
                    candidates.extend(page['results']);next_offset=page['next_offset']
                    if next_offset is None:break
                baseline=[h['passage_id'] for h in candidates];scored={};reason='no_candidates'
                if candidates:
                    if self.on_event:self.on_event({'type':'phase','text':f'Reranking {len(candidates)} passages on {self.reranker.config.device}'})
                    # Preserve evidence; no title guess, text rewrite or silent truncation.
                    try:scored=self.reranker.score([(relevance,h['text']) for h in candidates],self.cancel_event)
                    except Exception as error:scored={'error':'scoring_failure','detail':str(error)[:300]}
                    if not isinstance(scored,dict):scored={'error':'invalid_response'}
                    scores=scored.get('scores',[]);counts=scored.get('pair_tokens',[])
                    reason=scored.get('error')
                    if not reason and (len(scores)!=len(candidates) or any(type(s) not in (int,float) or not math.isfinite(s) for s in scores)):reason='invalid_scores'
                    if not reason and (scored.get('device')!=self.reranker.config.device or scored.get('gpu_initialized') is not (self.reranker.config.device!='cpu')):reason='device_unverified'
                    if not reason and (len(counts)!=len(candidates) or any(type(n) is not int or not 0<n<=self.reranker.config.max_pair_tokens for n in counts)):reason='pair_token_budget'
                    if not reason:
                        candidates=[dict(hit,rerank_score=scores[i]) for i,hit in sorted(enumerate(candidates),key=lambda pair:(-scores[pair[0]],pair[0]))]
                info={'applied':reason is None,'reason':reason,'device':self.reranker.config.device,'candidate_limit':self.reranker.config.candidates,
                      'relevance_query':relevance,'seconds':scored.get('seconds',0),'load_seconds':scored.get('load_seconds',0),'detail':scored.get('detail')}
                if self.on_event and reason and candidates:self.on_event({'type':'phase','text':'Reranker fallback to lexical order: '+reason})
                if len(self.pools)>=8:self.pools.pop(next(iter(self.pools)))
                self.pools[binding]={'base':first,'results':candidates,'info':info,'lexical_has_more':next_offset is not None}
                self.trace.append({'query':args.get('query'),'baseline_ids':baseline,'ranked_ids':[h['passage_id'] for h in candidates],
                                   'rerank':info,'scoring':scored})
            pool=self.pools[binding];hits=copy.deepcopy(pool['results'][offset:offset+limit]);end=offset+len(hits)
            return {**{k:v for k,v in pool['base'].items() if k not in ('results','next_offset','timings')},
                    'results':hits,'next_offset':end if hits and end<len(pool['results']) else None,
                    'match_scope':'candidate_pool','candidate_count':len(pool['results']),'lexical_has_more':pool['lexical_has_more'],
                    'rerank':dict(pool['info'])}
        finally:self.rerank_seconds+=max(0,time.perf_counter()-started-(self.fts_seconds-before_fts))
