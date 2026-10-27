"""Read-only SQLite filenames, deliberately separate from browser file URLs."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path, PureWindowsPath
from urllib.parse import quote


def readonly_uri(path,immutable=False):
    # A UNC server belongs in the path, leaving URI authority empty.
    value=path if isinstance(path,PureWindowsPath) else Path(path).expanduser().resolve()
    if isinstance(value,PureWindowsPath) and not value.is_absolute():raise ValueError('SQLite path must be absolute')
    if str(value).startswith('\\\\'):
        uri='file://'+quote(value.as_posix(),safe='/:')
    else:uri=value.as_uri()
    return uri+'?mode=ro'+('&immutable=1' if immutable else '')


def network_path(path):
    value=str(path)
    if value.startswith(('\\\\','//')):return True
    if os.name=='nt':
        import ctypes
        drive=Path(path).anchor
        if drive:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetDriveTypeW.argtypes=[ctypes.c_wchar_p]
            kernel.GetDriveTypeW.restype=ctypes.c_uint
            return kernel.GetDriveTypeW(drive)==4
    return False


def connect_readonly(path,immutable=False,timeout=10):
    try:
        db=sqlite3.connect(readonly_uri(path,immutable),uri=True,timeout=timeout)
        try:db.execute('PRAGMA query_only=ON')
        except Exception:db.close();raise
        return db
    except sqlite3.Error as error:
        with closing(sqlite3.connect(':memory:')) as diagnostic:
            options=[row[0] for row in diagnostic.execute('PRAGMA compile_options')]
        raise sqlite3.OperationalError(f'Read-only SQLite open failed: {error}; SQLite {sqlite3.sqlite_version}; compile options: '+', '.join(options)) from error
