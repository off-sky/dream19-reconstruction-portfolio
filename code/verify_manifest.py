#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,sys
r=Path(__file__).resolve().parents[1];m=json.loads((r/'SHA256.json').read_text());bad=[]
for f,h in m.items():
 p=r/f
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:bad.append(f)
if bad:raise SystemExit('HASH FAIL: '+', '.join(bad))
print(f'PASS {len(m)} frozen files')
