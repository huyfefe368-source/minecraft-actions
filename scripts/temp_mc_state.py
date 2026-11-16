"""Exact, uncompressed server tree manifests shared by runner and VPS publisher."""
import hashlib
from pathlib import Path

def inventory(root,guard=None):
    result={}
    for path in sorted(Path(root).rglob('*')):
        if guard:guard()
        if path.is_symlink():
            raise ValueError('Symlink is not a server snapshot: '+str(path))
        if path.is_file():
            relative=path.relative_to(root).as_posix()
            if '.git' in path.relative_to(root).parts:
                raise ValueError('Nested Git metadata is not server data')
            h=hashlib.sha256()
            with path.open('rb') as src:
                for chunk in iter(lambda:src.read(8*1024*1024),b''):
                    if guard:guard()
                    h.update(chunk)
            result[relative]={'size':path.stat().st_size,'sha256':h.hexdigest()}
    return result

def verify(root, expected):
    if inventory(root)!=expected:
        raise ValueError('Snapshot incomplete or checksum mismatch')
