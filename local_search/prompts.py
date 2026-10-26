"""UTF-8 named prompt sections, loaded once per operation without interpolation."""
import re
from pathlib import Path

DEFAULT_PROMPTS=Path(__file__).resolve().parent.parent/'prompts.txt'
HEADER=re.compile(r'\[([a-z][a-z0-9_.-]*)\]')


def load_prompts(path=None):
    path=Path(path) if path is not None else DEFAULT_PROMPTS
    if path.stat().st_size>1024*1024:raise ValueError('Prompts file exceeds 1 MiB: '+str(path))
    sections={};name=None;lines=[]
    def finish():
        if name is not None:
            body='\n'.join(lines).strip()
            if not body:raise ValueError('Empty prompt section: '+name)
            sections[name]=body
    for number,line in enumerate(path.read_text('utf-8-sig').splitlines(),1):
        header=HEADER.fullmatch(line.strip())
        if header:
            finish();name=header.group(1);lines=[]
            if name in sections:raise ValueError('Duplicate prompt section: '+name)
        elif name is None:
            if line.strip() and not line.lstrip().startswith('#'):
                raise ValueError(f'Expected a [section.name] header at line {number}')
        else:lines.append(line)
    finish()
    return sections


def load_prompt(name,path=None):
    sections=load_prompts(path)
    if name not in sections:raise ValueError('Missing prompt section: '+name)
    return sections[name]
