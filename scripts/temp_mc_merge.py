"""Content-hashed three-way merge, with indivisible Minecraft world snapshots."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from temp_mc_state import inventory, verify

WORLDS = ('world', 'world_nether', 'world_the_end')


def _validate_paths(files):
    for name in files:
        if not isinstance(name, str) or not name or '\\' in name:
            raise ValueError('Unsafe snapshot path: ' + repr(name))
        parts = name.split('/')
        if PurePosixPath(name).is_absolute() or any(p in ('', '.', '..', '.git') for p in parts):
            raise ValueError('Unsafe snapshot path: ' + repr(name))
    names = set(files)
    for name in names:
        if any(parent.as_posix() in names for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('File/directory collision: ' + name)


def _safe_root(root):
    root = Path(root).absolute()
    for path in (root, *root.parents):
        if path.is_symlink():
            raise ValueError('Symlink in snapshot root: ' + str(path))
    return root


def _subtree(files, world):
    return {p: v for p, v in files.items() if p == world or p.startswith(world + '/')}


def _copy(files, source, destination):
    for name in files:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, path)


def merge_trees(base, local, remote, target, conflicts):
    """Build a fresh target; return its inventory, remote changes and conflicts.

    Conflicts contain full side copies and a deletions.json mapping each side
    to paths absent on that side. Invalid plans fail before writing outputs.
    Inputs must remain quiescent; final checksum checks detect changed inputs.
    """
    _validate_paths(base)
    local, remote, target, conflicts = map(_safe_root, (local, remote, target, conflicts))
    roots = (local, remote, target, conflicts)
    for i, root in enumerate(roots):
        for other in roots[i + 1:]:
            if root == other or root in other.parents or other in root.parents:
                raise ValueError('Merge roots must be disjoint')
    for destination in (target, conflicts):
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError('Merge output must be absent or empty: ' + str(destination))
    for source in (local, remote):
        if not source.is_dir():
            raise ValueError('Missing snapshot directory: ' + str(source))
    for source in (local, remote):
        for path in source.rglob('*'):
            if '.git' in path.relative_to(source).parts:
                raise ValueError('Git metadata is not server data')
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('Unsupported snapshot entry: ' + str(path))
    left, right = inventory(local), inventory(remote)
    _validate_paths(left); _validate_paths(right)
    selected = {}
    conflict_names = []
    preserved = {'local': {}, 'remote': {}}
    deleted = {'local': [], 'remote': []}
    handled = set()

    def preserve(name, names):
        conflict_names.append(name)
        for side, files in (('local', left), ('remote', right)):
            present = {p: files[p] for p in names if p in files}
            preserved[side].update(present)
            deleted[side].extend(p for p in names if p not in files)

    for world in WORLDS:
        b, l, r = (_subtree(files, world) for files in (base, left, right))
        if r != b:
            names = set(b) | set(l) | set(r)
            handled.update(names)
            selected.update({p: ('remote', value) for p, value in r.items()})
            if l != b and l != r:
                preserve(world, names)
    for name in sorted((set(base) | set(left) | set(right)) - handled):
        b, l, r = base.get(name), left.get(name), right.get(name)
        if l == r or r == b:
            side, value = 'local', l
        elif l == b:
            side, value = 'remote', r
        else:
            preserve(name, {name})
            side = 'local' if name.startswith(('logs/', '.sync-conflicts/')) else 'remote'
            value = left.get(name) if side == 'local' else right.get(name)
        if value is not None:
            selected[name] = (side, value)
    _validate_paths(selected)
    for files in preserved.values():
        _validate_paths(files)
    expected = {p: value for p, (_, value) in selected.items()}
    applied = sorted(p for p in set(left) | set(expected) if left.get(p) != expected.get(p))
    target.parent.mkdir(parents=True, exist_ok=True)
    conflicts.parent.mkdir(parents=True, exist_ok=True)
    # Stage both outputs and verify bytes before making the merged tree visible.
    with tempfile.TemporaryDirectory(prefix='.merge-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'target'; stage.mkdir()
        evidence = Path(temporary) / 'conflicts'; evidence.mkdir()
        for side, source in (('local', local), ('remote', remote)):
            _copy([p for p, (s, _) in selected.items() if s == side], source, stage)
            side_dir = evidence / side; side_dir.mkdir()
            _copy(preserved[side], source, side_dir)
            verify(side_dir, preserved[side])
        verify(stage, expected)
        verify(local, left); verify(remote, right)
        (evidence / 'deletions.json').write_text(json.dumps({k: sorted(set(v)) for k, v in deleted.items()}, indent=2) + '\n')
        # Evidence is committed first, so a failed target rename never loses it.
        shutil.copytree(evidence, conflicts, dirs_exist_ok=True)
        os.replace(stage, target)
    return {'files': expected, 'applied_remote': applied, 'conflicts': sorted(conflict_names)}
