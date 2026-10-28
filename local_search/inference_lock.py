"""One supported GGUF owner across the desktop, CLI and metadata sidecar."""
import os
from pathlib import Path


class InferenceLease:
    def __init__(self,root=None):
        directory=Path(root) if root else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'LocalLibrarySearch'
        directory.mkdir(parents=True,exist_ok=True);self.stream=(directory/'inference.lock').open('a+b')
        try:
            self.stream.seek(0);self.stream.write(b'0');self.stream.flush();self.stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close();raise ValueError('Another app/metadata worker owns the local model. Unload it before starting this model.') from None

    def close(self):self.stream.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
