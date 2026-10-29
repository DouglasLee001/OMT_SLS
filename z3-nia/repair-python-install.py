#!/usr/bin/env python3
"""Make CMake-installed Python library links resolve within the local install."""
from pathlib import Path
import os

root = Path(__file__).resolve().parent
package = root / 'install/lib/python3.12/site-packages/z3'
for link in package.glob('libz3.so*'):
    if link.is_symlink():
        target = root / 'install/lib' / link.name
        assert target.is_file(), target
        relative = os.path.relpath(target, link.parent)
        before = os.readlink(link)
        link.unlink()
        link.symlink_to(relative)
        print(str(link.relative_to(root)) + ': ' + before + ' -> ' + relative)
        assert link.resolve().is_relative_to(root / 'install')
