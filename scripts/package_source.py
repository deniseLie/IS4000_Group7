#!/usr/bin/env python3
"""Create a source-only ZIP: the files Git tracks (uncommitted edits included), never settings, logins or caches.

What is included and why: docs/datasets.md. Without Git (e.g. inside an unpacked ZIP) it walks the folder and skips
the same things. Run from a Claude worktree, it still writes the ZIP beside the main checkout, never into .claude/.

Usage: python scripts/package_source.py [--output path/to/IS4000_Group7-Source.zip]
"""
from pathlib import Path
import argparse
import fnmatch
import os
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'IS4000_Group7'  # the top folder inside the ZIP, whatever the checkout or worktree folder is called
EXCLUDE_DIRS = {
    '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.vite', 'dist', 'build',
    '__MACOSX', 'test-results', 'playwright-report', '.cache', 'htmlcov',
    '.idea', '.vscode', '.claude', '.specify', 'specs', 'state', 'Video_DEMO',
}
EXCLUDE_FILES = {
    '.DS_Store', 'personas.json', '.coverage', 'evaluation-results.json', 'CLAUDE.md',
}
EXCLUDE_GLOBS = ('._*', '*.pyc', '*.pyo', '*.log', '*-Source.zip', '*.egg-info', '.fuse_hidden*', '*.stackdump',
                 '*.pptx', '*.mp4', '*.mov', '*.zip', '*.tgz')


def excluded(name):
    return (name in EXCLUDE_FILES
            or (name.startswith('.env') and name != '.env.example')
            or any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDE_GLOBS))


def allowed(relative):
    parts = Path(relative).parts
    return not any(part in EXCLUDE_DIRS or excluded(part) for part in parts)


def git(*args):
    try:
        return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def tracked():
    """Paths Git tracks, relative to ROOT, or None when this is not a Git checkout."""
    listed = git('ls-files', '-z')
    return None if listed is None else [path for path in listed.decode('utf-8').split('\0') if path]


def default_output():
    """Beside the main checkout: a worktree under .claude/worktrees/ shares its .git, so the ZIP never lands in .claude/."""
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir')
    main = Path(common.decode('utf-8').strip()).parent if common else ROOT
    return main.parent / (NAME + '-Source.zip')


def walked():
    for directory, dirs, files in os.walk(ROOT, followlinks=False):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and not excluded(d) and not (base / d).is_symlink())
        for name in sorted(files):
            yield (base / name).relative_to(ROOT).as_posix()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, help='where to write the ZIP (default: beside the main checkout)')
    output = (parser.parse_args().output or default_output()).resolve()
    if ROOT.resolve() in output.parents:
        raise SystemExit('Write the ZIP outside the project folder, or it would pack itself')
    paths = tracked()
    source = 'tracked by Git' if paths is not None else 'found on disk (no Git)'
    count = 0
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(paths if paths is not None else walked()):
            path = ROOT / relative
            if not allowed(relative) or path.is_symlink() or not path.is_file():
                continue  # excluded, or deleted but not yet committed
            archive.write(path, f'{NAME}/{relative}')
            count += 1
    print(f'Created {output}: {output.stat().st_size / 1_000_000:.2f} MB, {count} files {source}')


if __name__ == '__main__':
    main()
