"""Ingestion progress snapshots and local, rotating failure logs."""
import logging
from logging.handlers import RotatingFileHandler
import sys
import time


def terminal(message):
    stream=sys.stderr
    if stream is None:return
    try:
        encoding=getattr(stream,'encoding',None) or 'utf-8'
        print(message.encode(encoding,errors='backslashreplace').decode(encoding),file=stream,flush=True)
    except (OSError,ValueError):pass


def format_progress(event):
    stats=event.get('stats',{});total=event.get('total')
    count=str(event.get('processed',0))+('/'+str(total) if total is not None else ' (total not yet known)')
    return (f"[{event['collection']}] {event['phase']} · processed {count} · "
            f"indexed {stats.get('indexed',0)}, cached {stats.get('cached',0)}, "
            f"extracted {stats.get('extracted',0)}, skipped {stats.get('skipped',0)}, "
            f"errors {stats.get('errors',0)} · {event['elapsed_seconds']:.1f}s"+
            (' · '+event['current'] if event.get('current') else ''))


class Progress:
    def __init__(self,collection,stats,callback=None,total=None):
        self.collection=collection;self.stats=stats;self.callback=callback;self.total=total
        self.started=time.monotonic();self.last=0;self.processed=0;self.current=None

    def emit(self,phase,current=None,force=False):
        if current is not None:self.current=str(current)
        now=time.monotonic()
        if self.callback and (force or now-self.last>=.1):
            self.last=now
            self.callback({'type':'ingest_progress','collection':self.collection,'phase':phase,
                           'current':self.current,'processed':self.processed,'total':self.total,
                           'stats':dict(self.stats),'elapsed_seconds':now-self.started})


class FailureLog:
    def __init__(self,path):
        self.path=path;self.handler=None
        self.logger=logging.Logger('local-search-ingest',level=logging.WARNING)
        try:
            self.handler=RotatingFileHandler(path,maxBytes=5*1024*1024,backupCount=2,encoding='utf-8')
            self.handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.logger.addHandler(self.handler)
        except OSError as error:terminal('Cannot open ingestion log: '+str(error))

    def failure(self,collection,path,reason,kind='ERROR'):
        # repr keeps filenames/reasons on one line even if source text contains newlines.
        message=f'{kind} [{collection}] {str(path)!r}: {str(reason)!r}'
        terminal(message)
        if self.handler:self.logger.warning(message)

    def close(self):
        if self.handler:self.handler.close();self.logger.removeHandler(self.handler)


class TerminalProgress:
    def __init__(self):self.last=0
    def __call__(self,event):
        now=time.monotonic()
        if now-self.last>=.5 or event['phase'] in ('Starting','Complete','Source failed'):
            self.last=now;terminal(format_progress(event))
