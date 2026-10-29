"""Direct GGUF inference with PyTorch DLL discovery and exact native token counts."""
import importlib.util
import json
import os
import ctypes
import time
from pathlib import Path


class DLLDirectories:
    def __init__(self,torch_lib=None):
        self.handles=[];self.paths=[];self.loaded=[]
        if os.name!='nt':return
        if torch_lib:
            candidates=[Path(torch_lib).expanduser().resolve()]
            if not candidates[0].is_dir():raise ValueError('Configured torch_lib directory does not exist')
        else:
            spec=importlib.util.find_spec('torch')
            candidates=[Path(spec.origin).parent/'lib'] if spec and spec.origin else []
        for path in candidates:
            if path.is_dir():
                # Hold handles for the model lifetime; closing one removes its search directory.
                self.handles.append(os.add_dll_directory(str(path)));self.paths.append(str(path))
                for pattern in ('cudart64*.dll','cublasLt64*.dll','cublas64*.dll'):
                    for dll in sorted(path.glob(pattern)):
                        self.loaded.append(ctypes.WinDLL(str(dll)))

    def close(self):
        for handle in self.handles:handle.close()
        self.handles=[]


class GGUF:
    def __init__(self,path,context=12000,output=2000,gpu_layers=0,torch_lib=None,thinking=False,threads=None,
                 cache_k='f16',cache_v='f16',flash_attention=False,kv_offload=True):
        load_started=time.perf_counter();path=Path(path).expanduser().resolve()
        if path.suffix.lower()!='.gguf' or not path.is_file():raise ValueError('Select an existing GGUF file')
        if context<1024 or output<64 or output>=context:raise ValueError('Invalid context/output limits')
        validate_cache(cache_k,cache_v,flash_attention)
        self.context=context;self.output=output;self.thinking=thinking;self.dlls=DLLDirectories(torch_lib)
        try:
            from llama_cpp import Llama
            import llama_cpp
            from llama_cpp import llama_supports_gpu_offload
            from llama_cpp.llama_chat_format import Jinja2ChatFormatter
            if gpu_layers!=0 and not llama_supports_gpu_offload():
                raise ValueError('Installed llama-cpp-python is CPU-only. PyTorch DLLs cannot add a CUDA backend; install a compatible CUDA build or use gpu_layers=0.')
            kwargs={'n_threads':threads} if threads else {}
            types={name:getattr(llama_cpp,'GGML_TYPE_'+name.upper()) for name in ('f16','q8_0','q4_0')}
            from .diagnostics import load_observations,offloaded_layers
            with load_observations(llama_cpp) as observations:
                self.llm=Llama(model_path=str(path),n_ctx=context,n_gpu_layers=gpu_layers,verbose=False,
                    type_k=types[cache_k],type_v=types[cache_v],flash_attn=flash_attention,offload_kqv=kv_offload,**kwargs)
            template=self.llm.metadata.get('tokenizer.chat_template')
            if not template:raise ValueError('GGUF is missing tokenizer.chat_template; use an instruction model with embedded chat template')
            self.formatter=Jinja2ChatFormatter(template=template,
                eos_token=self.llm.detokenize([self.llm.token_eos()],special=True).decode('utf-8'),
                bos_token=self.llm.detokenize([self.llm.token_bos()],special=True).decode('utf-8'))
            self.model_diagnostics={'model_file':path.name,'model_bytes':path.stat().st_size,'runtime':getattr(llama_cpp,'__version__','unknown'),
                'gpu_offload_supported':bool(llama_supports_gpu_offload()),'requested_gpu_layers':gpu_layers,
                'actual_gpu_layers':0 if gpu_layers==0 else None,
                'offload_verification':'CPU layers explicitly requested' if gpu_layers==0 else 'Runtime does not expose verified layer placement through this wrapper',
                'context_tokens':context,'output_tokens':output,'cache_k':cache_k,'cache_v':cache_v,
                'kv_offload_requested':kv_offload,'flash_attention_requested':flash_attention,'thinking':thinking,
                'mmap_requested':True,'load_seconds':time.perf_counter()-load_started}
            self.model_diagnostics['runtime_observations']=observations
            verified=offloaded_layers(observations)
            if verified:self.model_diagnostics.update(verified,offload_verification='Observed runtime offloaded-layer log')
        except Exception:
            if hasattr(self,'llm'):self.llm.close()
            self.dlls.close();raise

    def close(self):self.llm.close();self.dlls.close()

    def diagnostics(self):
        return dict(getattr(self,'model_diagnostics',{}),last_generation=getattr(self,'last_timing',None),
                    memory_note='System RAM and dedicated VRAM are separate. Shared GPU memory uses system RAM; throughput alone does not verify layer placement.')

    def tokens(self,messages):
        rendered=self.formatter(messages=messages,enable_thinking=self.thinking)
        # The template already adds model-specific special tokens. Pass token IDs directly to generation.
        return self.llm.tokenize(rendered.prompt.encode('utf-8'),add_bos=False,special=True),rendered

    def count(self,messages):return len(self.tokens(messages)[0])

    def complete_json(self,messages,schema,on_event=None):
        return self.complete(messages,on_event=on_event,json_schema=schema)

    def complete(self,messages,on_event=None,json_schema=None):
        started=time.perf_counter()
        tokens,rendered=self.tokens(messages)
        if len(tokens)+self.output>self.context:raise ValueError('Prompt plus output reserve exceeds the configured context cap')
        criteria=rendered.stopping_criteria
        if getattr(self,'cancel_event',None) is not None:
            from llama_cpp import StoppingCriteriaList
            criteria=StoppingCriteriaList(list(criteria or [])+[lambda tokens,logits:self.cancel_event.is_set()])
        options=dict(prompt=tokens,max_tokens=self.output,temperature=0,stop=rendered.stop,stopping_criteria=criteria)
        constrained=False
        if json_schema is not None and not self.thinking:
            from llama_cpp import LlamaGrammar
            options['grammar']=LlamaGrammar.from_json_schema(json.dumps(json_schema),verbose=False);constrained=True
        runtime_started=time.perf_counter()
        if on_event is None:
            response=self.llm.create_completion(**options)
            ended=time.perf_counter()
        else:
            on_event({'type':'phase','text':'Processing prompt / generating'})
            chunks=[];finish=None;first=None
            for chunk in self.llm.create_completion(**options,stream=True):
                choice=chunk['choices'][0];text=choice.get('text','')
                if text:
                    if first is None:first=time.perf_counter()
                    chunks.append(text);on_event({'type':'text','text':text})
                if choice.get('finish_reason') is not None:finish=choice['finish_reason']
            text=''.join(chunks);ended=time.perf_counter()
            # Public tokenizer only: approximate because token boundaries can differ on re-tokenization.
            generated=len(self.llm.tokenize(text.encode('utf-8'),add_bos=False))
            response={'choices':[{'text':text,'finish_reason':finish}],
                      'usage':{'prompt_tokens':len(tokens),'completion_tokens':generated,'total_tokens':len(tokens)+generated},
                      'timings':{'first_output_seconds':first-started if first is not None else None,
                                 'approx_decode_tokens_per_second':max(0,generated-1)/(ended-first) if first is not None and ended>first and generated>1 else None,
                                 'token_count_approximate':True}}
        timing=response.setdefault('timings',{})
        timing['preparation_seconds']=runtime_started-started
        timing['inference_seconds']=ended-runtime_started
        timing['total_seconds']=time.perf_counter()-started
        timing['postprocessing_seconds']=max(0,timing['total_seconds']-timing['preparation_seconds']-timing['inference_seconds'])
        response['reserved_total']=len(tokens)+self.output
        if json_schema is not None:response['json_schema_constrained']=constrained
        self.last_timing=dict(timing)
        if on_event:on_event({'type':'stream_end'})
        return response


def parse_action(text):
    # One top-level action only; do not accidentally execute a nested/second action.
    text=text.strip()
    if not text.startswith('{') and '</think>' in text:text=text.rsplit('</think>',1)[1].strip()
    if text.startswith('```json') and text.endswith('```'):text=text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):text=text[3:-3].strip()
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Duplicate JSON key: '+key)
            value[key]=item
        return value
    try:value=json.loads(text,object_pairs_hook=unique,parse_constant=lambda item:(_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    except (ValueError,TypeError) as error:raise ValueError('Model did not return exactly one JSON action: '+str(error)) from error
    if not isinstance(value,dict) or 'action' not in value:raise ValueError('Model did not return a top-level JSON action')
    return value


def validate_cache(cache_k,cache_v,flash_attention):
    if cache_k not in ('f16','q8_0','q4_0') or cache_v not in ('f16','q8_0','q4_0'):
        raise ValueError('K/V cache types must be f16, q8_0 or q4_0')
    if cache_v!='f16' and not flash_attention:
        raise ValueError('Quantized V cache requires Flash Attention. Enable it or select f16 for V.')
