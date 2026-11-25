"""Experimental host-side evidence binding; this is NOT a browser observer.

All inputs, including images, must come from a genuinely trusted observer and
the signing key must be inaccessible to the candidate. A valid signature proves
only that the key signed these bytes, not that a browser produced them, that the
executed program matched the supplied hash, or that the task succeeded. This
module is not connected to the pipeline's verification gate.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

MAX_ARTIFACT_BYTES = 5_000_000
MAX_TASK_BYTES = 64_000
MAX_SCRIPT_BYTES = 5_000_000
MAX_CAPTURES = 5  # before + after + up to three action-time images
ROLES = {'before', 'action', 'after', 'observation'}
ID_RE = re.compile(r'[a-zA-Z0-9_-]{1,64}\Z')
SHA_RE = re.compile(r'[a-f0-9]{64}\Z')


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _key(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('signing key must be at least 32 bytes')


def _bounded(data, limit):
    if not isinstance(data, bytes) or len(data) > limit:
        raise ValueError('invalid or oversized artifact')
    return data


def _write_new(root: Path, name: str, data: bytes):
    # Artifact names are host-defined single path components. O_EXCL avoids
    # replacing prior files; O_NOFOLLOW defends the final component.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(root / name, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        raise


def create_capsule(output: Path, *, run_id: str, task: dict, script: bytes,
                   captures: list, key: bytes, evidence_metadata: dict | None = None):
    """Sign host-supplied bytes into a new output directory, never a live action.

    Output parent must be trusted and outside the candidate's write access. The
    function does not enforce that isolation: the caller must provide it.
    """
    _key(key)
    if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
        raise ValueError('invalid run id')
    if not isinstance(task, dict):
        raise ValueError('invalid task')
    task_data = _bounded(_json(task), MAX_TASK_BYTES)
    script_data = _bounded(script, MAX_SCRIPT_BYTES)
    if evidence_metadata is None:evidence_metadata={}
    if not isinstance(evidence_metadata,dict):raise ValueError('invalid evidence metadata')
    _bounded(_json(evidence_metadata), MAX_TASK_BYTES)
    if not isinstance(captures, list) or not 1 <= len(captures) <= MAX_CAPTURES:
        raise ValueError('invalid capture count')
    validated=[]
    for item in captures:
        if not isinstance(item, dict) or set(item) != {'role','data'} or item['role'] not in ROLES:
            raise ValueError('invalid capture')
        data=_bounded(item['data'], MAX_ARTIFACT_BYTES)
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('capture must have a PNG signature')
        validated.append((item['role'],data))
    # Capture files and manifest are only written after all input validation.
    root=Path(output)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    artifacts=[]
    for index,(role,data) in enumerate(validated):
        name=f'capture_{index:02d}.png'
        _write_new(root,name,data)
        artifacts.append({'name':name,'role':role,'sha256':_hash(data),'bytes':len(data)})
    body={'version':1,'run_id':run_id,'task_sha256':_hash(task_data),'script_sha256':_hash(script_data),
          'captures':artifacts,'evidence_metadata':evidence_metadata,
          'limitations':['observer and execution provenance not established by signature',
                                               'not eligible for automatic verification']}
    signature=hmac.new(key,_json(body),hashlib.sha256).hexdigest()
    _write_new(root,'manifest.json',_json({'body':body,'hmac_sha256':signature})+b'\n')
    return body


def _read_regular(root: Path, name: str, limit: int) -> bytes:
    if not re.fullmatch(r'(?:manifest\.json|capture_\d{2}\.png)',name):
        raise ValueError('invalid artifact name')
    fd=os.open(root / name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ValueError('artifact is not a bounded regular file')
        with os.fdopen(fd,'rb') as stream:
            data=stream.read(limit+1)
            if len(data)>limit: raise ValueError('artifact too large')
            return data
    except BaseException:
        try: os.close(fd)
        except OSError: pass
        raise


def verify_capsule(root: Path, *, run_id: str, task: dict, script: bytes, key: bytes,
                   expected_metadata: dict | None = None):
    """Return integrity result; success is never an independently verified task."""
    result={'integrity':'invalid','eligible_for_pipeline_gate':False,'is_ground_truth':False}
    try:
        _key(key)
        if not isinstance(run_id,str) or not ID_RE.fullmatch(run_id) or not isinstance(task,dict):
            raise ValueError('invalid expected run or task')
        root=Path(root)
        if root.is_symlink() or not root.is_dir(): raise ValueError('invalid capsule root')
        manifest=json.loads(_read_regular(root,'manifest.json',64_000))
        if not isinstance(manifest,dict) or set(manifest)!={'body','hmac_sha256'}:
            raise ValueError('invalid manifest')
        body=manifest['body']; signature=manifest['hmac_sha256']
        if not isinstance(body,dict) or not isinstance(signature,str) or not SHA_RE.fullmatch(signature):
            raise ValueError('invalid signature shape')
        expected=hmac.new(key,_json(body),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected): raise ValueError('signature mismatch')
        if body.get('version')!=1 or body.get('run_id')!=run_id:
            raise ValueError('run mismatch')
        if body.get('task_sha256')!=_hash(_bounded(_json(task),MAX_TASK_BYTES)):
            raise ValueError('task mismatch')
        if body.get('script_sha256')!=_hash(_bounded(script,MAX_SCRIPT_BYTES)):
            raise ValueError('script mismatch')
        if expected_metadata is not None and body.get('evidence_metadata')!=expected_metadata:
            raise ValueError('evidence metadata mismatch')
        captures=body.get('captures')
        if not isinstance(captures,list) or not 1<=len(captures)<=MAX_CAPTURES:
            raise ValueError('invalid captures')
        names=set()
        for index,item in enumerate(captures):
            if (not isinstance(item,dict) or set(item)!={'name','role','sha256','bytes'} or
                item['name']!=f'capture_{index:02d}.png' or item['name'] in names or
                item['role'] not in ROLES or type(item['bytes']) is not int or
                not isinstance(item['sha256'],str) or not SHA_RE.fullmatch(item['sha256'])):
                raise ValueError('invalid capture entry')
            names.add(item['name']); data=_read_regular(root,item['name'],MAX_ARTIFACT_BYTES)
            if (len(data)!=item['bytes'] or _hash(data)!=item['sha256'] or
                not data.startswith(b'\x89PNG\r\n\x1a\n')):
                raise ValueError('capture mismatch')
        result.update(integrity='valid',reason='signature and supplied byte hashes match; observer provenance is unproven')
    except (OSError,ValueError,TypeError,KeyError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        result['reason']=type(exc).__name__
    return result
