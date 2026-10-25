"""Isolated text extraction entry point; no OCR or source writes."""
import json
import sys
from .ingest import extract_pdf

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(extract_pdf(sys.argv[1],json.loads(sys.argv[2])),ensure_ascii=False))
