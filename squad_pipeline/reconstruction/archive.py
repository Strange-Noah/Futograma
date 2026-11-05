"""Bounded retained-evidence resolution; missing captures remain explicitly unavailable.

The index is produced by source-specific parsers after strict canonical and player
identity checks. Search never interprets arbitrary source prose as a squad status.
"""
from pathlib import Path
import hashlib
import json
from .engine import Evidence, Resolution, SquadStatus, digest


class Archive:
    def __init__(self, root, records=()):
        self.root = Path(root).resolve()
        self.records = list(records)
        self.version = digest(self.records)
        self.checked = {}

    def body(self, path, sha256):
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError('SOURCE_PATH_OUTSIDE_AUTHORIZED_ARCHIVE')
        if not target.is_file():
            return None
        stat=target.stat()
        key = (str(target), sha256, stat.st_size, stat.st_mtime_ns)
        if key in self.checked:
            return self.checked[key]
        body = target.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError('SOURCE_BODY_HASH_MISMATCH')
        self.checked[key] = body
        return body

    def resolve(self, path, finding):
        candidates = [r for r in self.records if r['path_kind'] == path
            and str(r['player']) == finding['player'] and r['team'] == finding['team']
            and r.get('canonical') == finding.get('canonical')]
        available = []
        for r in candidates:
            if self.body(r['body_path'], r['evidence']['sha256']) is None:
                continue
            e = Evidence(**r['evidence'])
            if not e.valid():
                raise ValueError('INVALID_ARCHIVED_EVIDENCE')
            available.append((r, e))
        if not available:
            return Resolution(path, 'UNAVAILABLE', 'No retained hash-verified parsed claim for this exact player/team/fixture; live adapter still required')
        statuses = []
        for r, e in available:
            # Parser must attest exact fixture/provider/player binding, independently
            # of an untrusted arbitrary index field or matching URL.
            if not r.get('strict_identity_verified'):
                return Resolution(path, 'CONFLICT', 'Retained claim lacks exact source identity proof', (e,))
            if r.get('status'):
                item = r['status']
                statuses.append(SquadStatus(str(r['player']), r['canonical'], r['team'],
                    item['status'], item.get('minutes'), (e,), item.get('complete_squad',False)))
        if len({(s.status,s.minutes) for s in statuses}) > 1:
            return Resolution(path, 'CONFLICT', 'Conflicting retained exact-identity claims',tuple(e for _,e in available))
        if statuses:
            return Resolution(path, 'FOUND', 'Exact retained parsed squad claim', tuple(e for _,e in available), statuses[0])
        return Resolution(path, 'NO_MATCH', 'Retained scoped evidence checked; no certified squad status', tuple(e for _,e in available))
