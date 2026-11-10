"""Snapshots must flush through descriptors supported by Windows as well as POSIX."""

import os
import sqlite3
from contextlib import closing

from sftpwarden.utils.sqlite import snapshot_sqlite


def test_snapshot_flush_uses_a_writable_descriptor(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("create table users (name text)")
        connection.execute("insert into users values ('alice')")
        connection.commit()
    original = os.fsync
    flushed = []

    def writable_sync(descriptor):
        # A zero-byte write reproduces Windows' write-access requirement on POSIX.
        os.write(descriptor, b"")
        flushed.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", writable_sync)
    snapshot_sqlite(source, target)
    assert len(flushed) == 1
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("select name from users").fetchall() == [("alice",)]
