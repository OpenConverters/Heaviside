"""Importing the API module must NOT reconfigure the process's logging.

``heaviside/api/server.py`` routes the ``heaviside.*`` logger tree to stderr and
sets ``propagate = False`` so a served process does not double-log through
uvicorn's root handlers. That is correct *for a process that serves the app* —
but it used to run as an **import side effect**, so any process that merely
imported the module inherited it irreversibly.

In the test suite that meant: the moment pytest *collected* a module importing
``heaviside.api.server``, every ``heaviside.*`` log record stopped reaching the
root logger, where ``caplog``'s handler lives. Tests asserting on log output
then passed standalone and failed in the full suite — an order-dependent,
permanently amber suite (ABT #899). The concrete casualty was
``tests/unit/stages/test_bom_extract_guard.py::test_persistent_under_coverage_logs_known_incomplete``.

The invariant pinned here is import-time neutrality, checked in a FRESH
interpreter so this test cannot itself be perturbed by suite order.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("fastapi")

_PROBE = textwrap.dedent(
    """
    import json, logging

    before = logging.getLogger("heaviside")
    state_before = [before.propagate, len(before.handlers), before.level]

    import heaviside.api.server  # noqa: F401  (the import IS the subject)

    after = logging.getLogger("heaviside")
    print(json.dumps({
        "before": state_before,
        "propagate": after.propagate,
        "handlers": len(after.handlers),
        "level": after.level,
    }))
    """
)


def test_importing_api_server_does_not_touch_global_logging() -> None:
    """A fresh interpreter that imports the API module must find the
    ``heaviside`` logger exactly as it left it: still propagating to root, with
    no handler attached and no level forced."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    state = json.loads(proc.stdout.strip().splitlines()[-1])

    # Sanity: nothing had configured the logger before the import under test.
    assert state["before"] == [True, 0, logging.NOTSET], state

    assert state["propagate"] is True, (
        "importing heaviside.api.server disabled propagation on the 'heaviside' "
        "logger — that detaches every heaviside.* record from the root handlers "
        "(caplog included) for the whole process. Configure logging from the "
        "ASGI startup event, not at import time (ABT #899)."
    )
    assert state["handlers"] == 0, "import attached a log handler; startup owns that"
    assert state["level"] == logging.NOTSET, "import forced a level; startup owns that"


def test_logging_config_still_runs_on_app_startup() -> None:
    """Moving the call out of import must not LOSE it: a process that serves the
    app still configures ``heaviside.*`` logging, via the startup hook."""
    from heaviside.api import server

    hooks = getattr(server.app.router, "on_startup", [])
    assert server._configure_logging_on_startup in hooks, (
        "the logging configurator is no longer registered on app startup — a "
        "served process would emit no heaviside.* logs at all"
    )
    # ...and it must be the FIRST hook, so later hooks' lines are captured too.
    assert hooks[0] is server._configure_logging_on_startup


_APPLY_PROBE = textwrap.dedent(
    """
    import json, logging
    from heaviside.api.server import _configure_heaviside_logging

    _configure_heaviside_logging()
    hlog = logging.getLogger("heaviside")
    print(json.dumps({
        "propagate": hlog.propagate,
        "handlers": len(hlog.handlers),
        "level": hlog.level,
    }))
    """
)


def test_configurator_attaches_stderr_handler_when_called() -> None:
    """The configurator itself is unchanged: called (as startup does), it
    attaches the stderr handler, honours HEAVISIDE_LOG_LEVEL and detaches from
    root. (The level is pinned via the environment so a repo ``.env`` — which
    only fills UNSET vars — cannot make this non-deterministic.)"""
    env = {**os.environ, "HEAVISIDE_LOG_LEVEL": "WARNING"}
    proc = subprocess.run(
        [sys.executable, "-c", _APPLY_PROBE],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    state = json.loads(proc.stdout.strip().splitlines()[-1])
    assert state == {"propagate": False, "handlers": 1, "level": logging.WARNING}
