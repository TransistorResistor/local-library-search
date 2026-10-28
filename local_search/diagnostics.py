"""Best-effort runtime observations; missing data is never inferred from speed."""
import os
import re
import shutil
import subprocess
from contextlib import contextmanager


@contextmanager
def load_observations(runtime):
    lines=[];callback=None;restore=None
    if hasattr(runtime,'llama_log_callback') and hasattr(runtime,'llama_log_set'):
        try:
            from llama_cpp._logger import llama_log_callback as restore
            def observe(level,text,data):
                try:
                    value=text.decode('utf-8',errors='replace')
                    if level==3 or re.search(r'offload|buffer size|KV.*size|CUDA|Vulkan',value,re.I):
                        lines.append(value.strip()[:512]);del lines[:-100]
                    restore(level,text,data)
                except Exception:pass
            callback=runtime.llama_log_callback(observe)
            runtime.llama_log_set(callback,None)
        except (ImportError,AttributeError,TypeError):callback=None
    try:yield lines
    finally:
        if callback is not None and restore is not None:runtime.llama_log_set(restore,None)


def offloaded_layers(lines):
    matches=re.findall(r'offloaded\s+(\d+)\s*/\s*(\d+)\s+layers','\n'.join(lines),re.I)
    return {'actual_gpu_layers':int(matches[-1][0]),'runtime_total_layers':int(matches[-1][1])} if matches else {}


def memory_observations():
    result={}
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('peak_working_set','working_set','peak_paged_pool','paged_pool','peak_nonpaged_pool','nonpaged_pool','pagefile','peak_pagefile','private_bytes')]
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
            api=ctypes.WinDLL('psapi');api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
            if api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
                result.update(process_working_set_bytes=counters.working_set,process_private_bytes=counters.private_bytes)
        except (OSError,AttributeError):pass
    executable=shutil.which('nvidia-smi')
    if executable:
        try:
            response=subprocess.run([executable,'--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if response.returncode==0:result['gpu_device_totals_csv']=response.stdout.strip()[:2000]
        except (OSError,subprocess.TimeoutExpired):pass
    result['scope']='Process RAM and device-wide GPU totals; GPU totals include other apps and are not this model alone.'
    return result
