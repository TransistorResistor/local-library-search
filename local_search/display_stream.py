"""Display-only splitting of reasoning tags; never alters parser input."""
class DisplayStream:
    def __init__(self,thinking=False):
        self.channel='thinking' if thinking else 'action';self.buffer='';self.started=False

    def feed(self,text,final=False):
        self.buffer+=text;out=[]
        if not self.started and self.buffer.lstrip().startswith('{'):self.channel='action'
        if self.buffer.strip():self.started=True
        while self.buffer:
            positions=[(self.buffer.find(tag),tag) for tag in ('<think>','</think>') if tag in self.buffer]
            if positions:
                index,tag=min(positions)
                if index:out.append((self.channel,self.buffer[:index]))
                self.buffer=self.buffer[index+len(tag):];self.channel='thinking' if tag=='<think>' else 'action'
            else:
                hold=0
                if not final:
                    for n in range(1,min(8,len(self.buffer))+1):
                        if any(tag.startswith(self.buffer[-n:]) for tag in ('<think>','</think>')):hold=n
                emit=self.buffer[:-hold] if hold else self.buffer
                if emit:out.append((self.channel,emit))
                self.buffer=self.buffer[-hold:] if hold else '';break
        return out
