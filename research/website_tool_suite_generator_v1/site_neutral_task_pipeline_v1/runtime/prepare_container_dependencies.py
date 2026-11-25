"""Copy a bounded Python 3.12 runtime subset; never mount the host environment."""
from importlib import metadata
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
OUT=ROOT/'automated_runs/container_compat_20261117_v2'
SOURCE=Path('/shared/miniconda3/lib/python3.12/site-packages').resolve()
PACKAGES=('pydantic','pydantic_core','annotated_types','typing_extensions','typing_inspection',
          'httpx','httpcore','certifi','anyio','idna','h11','exceptiongroup',
          'PyYAML','Jinja2','MarkupSafe','Pillow')


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    deps=OUT/'deps'; deps.mkdir()
    copied={}; versions={}
    for name in PACKAGES:
        dist=metadata.distribution(name); versions[name]=dist.version
        if not dist.files: raise ValueError('missing distribution file list: '+name)
        for relative in dist.files:
            path=Path(str(relative))
            if path.is_absolute() or '..' in path.parts or '__pycache__' in path.parts or path.suffix=='.pyc':
                continue
            src=SOURCE/path
            # No symlinks or non-regular files, including Unix sockets, enter the snapshot.
            if src.is_symlink() or not src.is_file() or SOURCE not in src.resolve().parents:
                continue
            dst=deps/path; dst.parent.mkdir(parents=True,exist_ok=True)
            if not dst.exists(): shutil.copy2(src,dst)
            copied[str(path)]=hashlib.sha256(dst.read_bytes()).hexdigest()
    vendor=ROOT/'vendor/Webwright/src'
    for src in vendor.rglob('*'):
        if src.is_symlink(): raise ValueError('vendor tree contains a symlink')
        if not src.is_file() or '__pycache__' in src.parts or src.suffix=='.pyc': continue
        dst=OUT/'vendor'/src.relative_to(vendor); dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
    summary={'package_versions':versions,'dependency_file_count':len(copied),
             'file_sha256':copied,'source_interpreter':'/shared/miniconda3/bin/python',
             'scope':'trusted dependency snapshot for isolated compatibility test; no model calls'}
    (OUT/'manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'status':'prepared','packages':versions,'file_count':len(copied)}))


if __name__=='__main__': main()
