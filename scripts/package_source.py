#!/usr/bin/env python3
"""Create a source-only ZIP without dependencies, caches or local settings."""
from pathlib import Path
import fnmatch
import os
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / (ROOT.name + '-Source.zip')
EXCLUDE_DIRS = {
    '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.vite', 'dist', 'build',
    '__MACOSX', 'test-results', 'playwright-report', '.cache', 'htmlcov',
    '.idea', '.vscode',
}
EXCLUDE_FILES = {
    '.DS_Store', 'personas.json', '.coverage', 'evaluation-results.json',
}
EXCLUDE_GLOBS = ('._*', '*.pyc', '*.pyo', '*.log', '*-Source.zip', '*.egg-info')


def excluded(name):
    return (name in EXCLUDE_FILES
            or (name.startswith('.env') and name != '.env.example')
            or any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDE_GLOBS))


def main():
    count = 0
    with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for directory, dirs, files in os.walk(ROOT, followlinks=False):
            base = Path(directory)
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS
                             and not excluded(d) and not (base / d).is_symlink())
            for name in sorted(files):
                path = base / name
                if excluded(name) or path.is_symlink():
                    continue
                archive.write(path, str(Path(ROOT.name) / path.relative_to(ROOT)))
                count += 1
    print(f'Created {OUTPUT.name}: {OUTPUT.stat().st_size / 1_000_000:.2f} MB, {count} files')


if __name__ == '__main__':
    main()
