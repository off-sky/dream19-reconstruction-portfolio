import sys,zipfile,json,hashlib
from pathlib import Path
root=Path(sys.argv[1]);dest=Path(sys.argv[2]);files=[p for p in root.rglob('*') if p.is_file() and p.name!='SHA256SUMS.json'];sha={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files};(root/'SHA256SUMS.json').write_text(json.dumps(sha,indent=2)+'\n');files.append(root/'SHA256SUMS.json')
with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in sorted(files):z.write(p,arcname=str(Path(root.name)/p.relative_to(root)))
with zipfile.ZipFile(dest) as z:assert z.testzip() is None
print('RESULT:',dest)
