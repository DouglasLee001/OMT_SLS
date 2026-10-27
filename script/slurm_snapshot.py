"""Freeze the shared runtime used by every node of one Slurm experiment.

Only benchmark instances remain shared with the live repository. Executables,
Python sources, the converter SDK, libraries, and reference CSVs are real copies
whose recorded hashes are checked before a worker starts.
"""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess


# These libraries belong to the node's glibc/loader installation. Shipping them
# through LD_LIBRARY_PATH can mix libc and its dynamic loader, so leave them on
# the node. The tested nodes all have Ubuntu glibc 2.39-0ubuntu8.9.
_SYSTEM_LIBRARIES = {
    'libc.so.6', 'libm.so.6', 'libpthread.so.0', 'libdl.so.2', 'librt.so.1',
    'libutil.so.1', 'libanl.so.1', 'libresolv.so.2', 'libBrokenLocale.so.1',
}


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def native_libraries(solver):
    """Return ldd's non-glibc dependency names and resolved files.

    Keys preserve the requested SONAME, even if its file resolves through a
    versioned system symlink. Missing dependencies cannot form a usable bundle.
    """
    try:
        result = subprocess.run(['ldd', str(solver)], check=True, capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'cannot inspect native solver libraries: {error}') from error
    libraries = {}
    for line in result.stdout.splitlines():
        if '=> not found' in line:
            raise ValueError(f'native solver dependency is missing: {line.strip()}')
        match = re.match(r'\s*(\S+)\s+=>\s+(/\S+)\s+\(', line)
        if not match:
            continue  # linux-vdso and the absolute glibc loader line.
        name, path = match.groups()
        if name in _SYSTEM_LIBRARIES or name.startswith(('ld-linux', 'ld-musl')):
            continue
        if Path(name).name != name:
            raise ValueError(f'invalid native library name: {name}')
        libraries[name] = Path(path)
    if not libraries:
        raise ValueError('native solver has no resolved runtime libraries')
    return libraries


def create_snapshot(run: Path, repo_root: Path, solver: Path, converter: Path,
                    baseline: Path) -> dict:
    """Create run/source once and return absolute runtime paths and SHA256s."""
    run, repo_root = Path(run).resolve(), Path(repo_root).resolve()
    solver, converter, baseline = (Path(path).resolve() for path in (solver, converter, baseline))
    run.mkdir(parents=True, exist_ok=True)
    source = run / 'source'
    source.mkdir()  # A previous frozen experiment is never overwritten.
    files = {}

    def copy(original, destination):
        original, destination = Path(original), Path(destination)
        if not original.is_file():
            raise ValueError(f'snapshot source file is missing: {original}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination, follow_symlinks=True)
        files[destination.relative_to(run).as_posix()] = _digest(destination)
        return str(destination)

    frozen_solver = copy(solver, source / 'nia_ls')
    library_dir = source / 'lib'
    library_dir.mkdir()
    for name, path in sorted(native_libraries(solver).items()):
        if Path(name).name != name:
            raise ValueError(f'invalid native library name: {name}')
        copy(path, library_dir / name)

    frozen_converter = copy(converter, source / 'converter/omt_to_ls.py')
    sdk_source = converter.parent / 'z3_fork/build'
    sdk_target = source / 'converter/z3_fork/build'
    python_sources = sorted((sdk_source / 'python/z3').glob('*.py'))
    if not python_sources or not (sdk_source / 'python/z3/__init__.py').is_file():
        raise ValueError(f'converter Python SDK is missing: {sdk_source / "python/z3"}')
    for path in python_sources:
        copy(path, sdk_target / 'python/z3' / path.name)
    # Keep this fork library separate from native's system libz3.so.4. The
    # converter deliberately loads the matching fork bindings and libz3.so.
    copy(sdk_source / 'libz3.so', sdk_target / 'libz3.so')

    for path in sorted((repo_root / 'script').glob('*.py')):
        copy(path, source / 'script' / path.name)
    launcher = source / 'script/run_optimize_slurm.py'
    if not launcher.is_file():
        raise ValueError('Slurm launcher is missing from the frozen Python scripts')
    frozen_baseline = copy(baseline, source / 'result/competitor.csv')
    for group in ('original', 'new'):
        reference = repo_root / 'result' / group
        tables = sorted(reference.glob('*.csv'))
        if not tables:
            raise ValueError(f'reference CSVs are missing: {reference}')
        for path in tables:
            copy(path, source / 'result' / group / path.name)
    benchmarks = repo_root / 'new_benchmark'
    if not benchmarks.is_dir():
        raise ValueError(f'shared generated benchmarks are missing: {benchmarks}')
    (source / 'new_benchmark').symlink_to(benchmarks, target_is_directory=True)

    snapshot = {'solver': frozen_solver, 'converter': frozen_converter,
                'launcher': str(launcher), 'baseline': frozen_baseline,
                'library_dir': str(library_dir), 'files': files,
                'solver_sha256': files[Path(frozen_solver).relative_to(run).as_posix()],
                'converter_sha256': files[Path(frozen_converter).relative_to(run).as_posix()]}
    check_snapshot(run, snapshot)
    return snapshot


def check_snapshot(run, snapshot) -> None:
    """Reject missing, altered, symlinked, or live runtime dependencies."""
    run = Path(run).resolve()
    source = run / 'source'
    files = snapshot.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('snapshot has no frozen file hashes')
    for relative, expected in files.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'source':
            raise ValueError(f'snapshot hash path is outside its source directory: {relative}')
        current = run
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError(f'frozen runtime may not use a live symlink: {relative}')
        if not current.is_file():
            raise ValueError(f'frozen snapshot file is missing: {relative}')
        if _digest(current) != expected:
            raise ValueError(f'frozen snapshot file changed: {relative}')
    for field in ('solver', 'converter', 'launcher', 'baseline'):
        path = Path(snapshot.get(field, ''))
        if not path.is_absolute() or not path.is_relative_to(source):
            raise ValueError(f'snapshot {field} points outside frozen runtime')
        relative = path.relative_to(run).as_posix()
        if relative not in files:
            raise ValueError(f'snapshot {field} has no recorded hash')
    if Path(snapshot.get('library_dir', '')) != source / 'lib':
        raise ValueError('snapshot library directory points outside frozen runtime')
    for field in ('solver', 'converter'):
        relative = Path(snapshot[field]).relative_to(run).as_posix()
        if snapshot.get(field + '_sha256') != files[relative]:
            raise ValueError(f'snapshot {field} hash metadata disagrees')


def snapshot_environment(snapshot) -> dict:
    """Prepend only this run's copied libraries; preserve the parent env."""
    environment = os.environ.copy()
    previous = environment.get('LD_LIBRARY_PATH')
    environment['LD_LIBRARY_PATH'] = str(snapshot['library_dir']) + (os.pathsep + previous if previous else '')
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    return environment
