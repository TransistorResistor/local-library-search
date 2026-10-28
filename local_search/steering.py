"""Thread-safe question-local instructions, applied only at model-turn boundaries."""
import threading
import time


class SteeringQueue:
    def __init__(self):
        self.lock=threading.Lock();self.items=[];self.closed=False

    def put(self,text):
        if not isinstance(text,str) or not text.strip():raise ValueError('Enter a steering instruction')
        text=text.strip()
        with self.lock:
            if self.closed:raise ValueError('This question has finished; start a new question')
            if len(self.items)>=12 or sum(len(i['text']) for i in self.items)+len(text)>8000:
                raise ValueError('Steering limit reached (12 instructions / 8000 characters per question)')
            item={'id':len(self.items)+1,'text':text,'queued_at':time.time(),'applied_round':None}
            self.items.append(item);return dict(item)

    def apply(self,round_number):
        with self.lock:
            new=[]
            for item in self.items:
                if item['applied_round'] is None:item['applied_round']=round_number;new.append(dict(item))
            return new

    def finalise(self):
        """Atomically accept completion, or require another turn for queued input."""
        with self.lock:
            if any(i['applied_round'] is None for i in self.items):return False
            self.closed=True;return True

    def snapshot(self,close=False):
        with self.lock:
            if close:self.closed=True
            return [dict(i) for i in self.items]
