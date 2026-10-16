import sqlite3
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mission_control.control_plane_read import ControlPlaneReadModel, SourceUnavailable


@pytest.mark.parametrize("expires_in, expected_live", [(None, False), (-1, False), (60, True)])
def test_agent_liveness_requires_an_unexpired_lease(monkeypatch, expires_in, expected_live):
    now = datetime.now(UTC)
    row = {
        "agent": "fixture-agent", "product": "Fixture", "workstation_id": "fixture-ws",
        "current_atomic_task": None, "branch": "task/fixture",
        "heartbeat_at": now - timedelta(seconds=5), "lease_state": "ACTIVE",
        "lease_expires_at": None if expires_in is None else now + timedelta(seconds=expires_in),
    }

    class Cursor:
        def execute(self, *args):
            pass

        def fetchall(self):
            return [row]

    @contextmanager
    def connection():
        yield Cursor()

    model = ControlPlaneReadModel(dsn="")
    monkeypatch.setattr(model, "connection", connection)
    agent = model.agents()[0]
    assert agent["live"] is expected_live
    assert agent["state"] == ("ACTIVE" if expected_live else "OFFLINE")


def test_task_detail_is_not_limited_to_the_latest_200_tasks(monkeypatch):
    # These task SELECT/JOIN/LIMIT statements use SQL shared by SQLite and PostgreSQL.
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE workstations (
            workstation_id TEXT PRIMARY KEY, product TEXT, section TEXT, subsection TEXT,
            branch TEXT, worktree TEXT, base_sha TEXT
        );
        CREATE TABLE atomic_tasks (
            task_id TEXT PRIMARY KEY, workstation_id TEXT, title TEXT, status TEXT,
            owner TEXT, exact_sha TEXT, updated_at TEXT
        );
        INSERT INTO workstations VALUES (
            'fixture-ws', 'Fixture', '02-api', '01-baseline', 'subsection/02-api--01-baseline',
            '/fixture/worktree', 'base'
        );
        INSERT INTO atomic_tasks VALUES (
            'older-task', 'fixture-ws', 'Older fixture task', 'CERTIFIED', 'fixture-agent',
            'older-sha', '2020-01-01T00:00:00+00:00'
        );
    """)
    database.executemany(
        "INSERT INTO atomic_tasks VALUES (?, 'fixture-ws', ?, 'IN_PROGRESS', NULL, NULL, ?)",
        [(f"newer-{i}", f"Newer task {i}", "2026-01-01T00:00:00+00:00") for i in range(201)],
    )

    class Cursor:
        def __init__(self):
            self.cursor = database.cursor()

        def execute(self, statement, parameters):
            self.cursor.execute(statement.replace("%s", "?"), parameters)

        def fetchall(self):
            return [dict(row) for row in self.cursor.fetchall()]

        def fetchone(self):
            row = self.cursor.fetchone()
            return dict(row) if row is not None else None

    @contextmanager
    def connection():
        cursor = Cursor()
        try:
            yield cursor
        finally:
            cursor.cursor.close()

    model = ControlPlaneReadModel(dsn="")
    monkeypatch.setattr(model, "connection", connection)
    try:
        recent = model.tasks("Fixture")
        assert len(recent) == 200
        assert all(task["task_id"] != "older-task" for task in recent)
        task = model.task("older-task")
        assert task is not None
        assert task["title"] == "Older fixture task"
        assert task["repository"] == "Fixture"
        assert task["completion_percent"] == 100
        assert task["execution_contract"]["branch"] == "subsection/02-api--01-baseline"
        assert model.task("nonexistent") is None
    finally:
        database.close()


@pytest.mark.parametrize("inside_outer_except", [False, True])
@pytest.mark.parametrize("query_fails, rollback_fails, close_fails", [
    (True, True, False), (True, False, True), (False, True, False), (False, False, True),
])
def test_connection_cleanup_keeps_driver_failures_unavailable(
    monkeypatch, query_fails, rollback_fails, close_fails, inside_outer_except,
):
    class DriverError(RuntimeError):
        pass

    query_error = DriverError("synthetic query failure")
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            if query_fails:
                raise query_error

    class Connection:
        def set_session(self, *, readonly, autocommit):
            assert readonly is True and autocommit is False

        def cursor(self, **kwargs):
            return Cursor()

        def rollback(self):
            calls.append("rollback")
            if rollback_fails:
                raise DriverError("synthetic rollback failure")

        def close(self):
            calls.append("close")
            if close_fails:
                raise DriverError("synthetic close failure")

    driver = SimpleNamespace(Error=DriverError, connect=lambda *args, **kwargs: Connection())
    monkeypatch.setitem(sys.modules, "psycopg2", driver)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", SimpleNamespace(RealDictCursor=object))
    model = ControlPlaneReadModel(dsn="synthetic-fixture-only")
    def read():
        with pytest.raises(SourceUnavailable) as error, model.connection() as cursor:
            cursor.execute("SELECT 1")
        return error.value

    if inside_outer_except:
        try:
            raise ValueError("unrelated outer exception")
        except ValueError:
            error = read()
    else:
        error = read()
    assert calls == ["rollback", "close"]
    if query_fails:
        assert error.__cause__ is query_error


def test_cleanup_does_not_mask_a_callers_exception(monkeypatch):
    class DriverError(RuntimeError):
        pass

    calls = []

    class Connection:
        def set_session(self, **kwargs):
            pass

        @contextmanager
        def cursor(self, **kwargs):
            yield object()

        def rollback(self):
            raise DriverError("synthetic rollback failure")

        def close(self):
            calls.append("close")

    monkeypatch.setitem(
        sys.modules, "psycopg2",
        SimpleNamespace(Error=DriverError, connect=lambda *args, **kwargs: Connection()),
    )
    monkeypatch.setitem(sys.modules, "psycopg2.extras", SimpleNamespace(RealDictCursor=object))
    model = ControlPlaneReadModel(dsn="fixture")
    with pytest.raises(ValueError, match="caller failure"), model.connection():
        raise ValueError("caller failure")
    assert calls == ["close"]
