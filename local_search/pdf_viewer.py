"""Small, fixed viewer adapters; user configuration selects an executable, not code."""
import os
import shutil
import subprocess
from pathlib import Path


def find_viewer(viewer,executable=''):
    if viewer not in ('chrome','acrobat'):raise ValueError('PDF viewer must be chrome or acrobat')
    if executable:
        path=Path(executable).expanduser()
        return str(path.resolve()) if path.is_file() else None
    names=('chrome.exe',) if viewer=='chrome' else ('Acrobat.exe','AcroRd32.exe')
    for name in names:
        found=shutil.which(name)
        if found:return found
    roots=[os.environ.get(key,'') for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')]
    suffixes=['Google/Chrome/Application/chrome.exe'] if viewer=='chrome' else ['Adobe/Acrobat DC/Acrobat/Acrobat.exe','Adobe/Acrobat Reader DC/Reader/AcroRd32.exe']
    for root in filter(None,roots):
        for suffix in suffixes:
            candidate=Path(root)/suffix
            if candidate.is_file():return str(candidate)
    return None


def page_command(path,page,viewer,executable):
    if type(page) is not int or page<1:raise ValueError('PDF page must be a positive integer')
    if viewer=='chrome':return [executable,'--new-tab',Path(path).resolve().as_uri()+'#page='+str(page)]
    if viewer=='acrobat':return [executable,'/A','page='+str(page),str(Path(path).resolve())]
    raise ValueError('Unsupported PDF viewer')


def open_page(path,page,options):
    if not options.get('enabled'):return 'disabled'
    if type(page) is not int or page<1:return 'no unique cited page'
    try:
        viewer=options.get('viewer','acrobat');exe=find_viewer(viewer,options.get('executable',''))
        if not exe:return 'viewer executable unavailable'
        subprocess.Popen(page_command(path,page,viewer,exe),shell=False)
        return None
    except (OSError,ValueError) as error:return 'viewer launch failed: '+str(error)
