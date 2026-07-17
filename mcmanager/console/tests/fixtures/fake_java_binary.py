"""Builds a small OS-appropriate executable that stands in for the `java`
binary in tests: it tolerates the -Xms/-Xmx/-jar/nogui argument shape
process.start() always passes (Python's own CLI parser rejects `-jar`
outright, so we can't point JAVA_BIN_PATH at sys.executable directly) and
launches fake_server.py underneath."""
import os
import stat
import sys
from pathlib import Path

_FAKE_SERVER_PATH = Path(__file__).parent / "fake_server.py"


def create_fake_java_binary(tmp_path: Path) -> Path:
    if os.name == "posix":
        wrapper = tmp_path / "fake_java"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{_FAKE_SERVER_PATH}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    else:
        wrapper = tmp_path / "fake_java.bat"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{_FAKE_SERVER_PATH}" %*\r\n',
            encoding="utf-8",
        )
    return wrapper
