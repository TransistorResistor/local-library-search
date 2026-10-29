"""Shared normal access and exclusive replacement/maintenance on a local library."""
import os
from contextlib import contextmanager
from pathlib import Path


class LibraryGuard:
    def __init__(self,path,exclusive=False):
        self.stream=None
        path=Path(str(Path(path).resolve())+'.access-lock');path.parent.mkdir(parents=True,exist_ok=True)
        stream=path.open('a+b');self.stream=stream
        try:
            if path.stat().st_size==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            if os.name=='nt':
                import ctypes,msvcrt
                from ctypes import wintypes
                class Overlapped(ctypes.Structure):
                    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
                self.overlapped=Overlapped();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                if not kernel.LockFileEx(msvcrt.get_osfhandle(stream.fileno()),1|(2 if exclusive else 0),0,1,0,ctypes.byref(self.overlapped)):raise OSError('Library access lock unavailable')
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_NB|(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH))
        except BaseException:
            self.close();raise ValueError('Close other library windows/workers before maintenance; the library is currently in use') from None

    def close(self):
        if self.stream:self.stream.close();self.stream=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


@contextmanager
def maintenance(library):
    guard=getattr(library,'guard',None)
    if guard:guard.close()
    try:
        with LibraryGuard(library.path,exclusive=True):yield
    finally:
        if guard and not getattr(library,'closed',False):library.guard=LibraryGuard(library.path)
