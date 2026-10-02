"""One way to say "here is the file", shared by every OpenConverters MCP server.

The problem it solves (ABT #661, #656): a real EMI scan, a Gerber set or a 150k-point deck
must not travel through the tool arguments, because tool arguments travel through the model
context. The orchestrator already stores an upload once and hands out a reference — so the
servers need to accept a reference, and they need to accept THE SAME one, or the orchestrator
implements three.

The convention, deliberately one argument rather than a path field and a URI field:

    /home/alf/board.kicad_pcb        a path on the machine running the server
    file:///home/alf/board.kicad_pcb the same thing, spelled as a URI
    artifact://<id>                  resolved against <SERVER>_ARTIFACT_BASE
    https://host/path                fetched as-is

A local reference is used in place, never copied. A remote one is fetched to a temp file and
the caller is handed that path, because every engine behind these servers reads files, not
bytes — Faraday, Hertz and Kirchhoff all take a path today. Nothing is fetched twice within a
call, and nothing is cached across calls: an artifact store is the orchestrator's job, and a
second cache here would be one more thing to go stale.

Auth, when the artifact API needs it: <SERVER>_ARTIFACT_TOKEN is sent as a bearer token. It is
never logged and never echoed into a result.

This file is IDENTICAL in Faraday/mcp, Hertz/mcp and Kirchhoff/mcp. It is a marked copy, not a
fork: change it in one and copy it to the others, or the convention stops being one.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

# A fetched artifact is capped so a mistyped reference cannot exhaust the disk. It is the
# largest thing any of these engines legitimately reads (an ODB++ job, a long scan), not a
# guess at what is reasonable.
MAX_FETCH_BYTES = 512 * 1024 * 1024


def _env(prefix: str, name: str) -> str:
    return os.environ.get(f"{prefix}_{name}", "").strip()


def display_name(reference: str) -> str:
    """What to CALL the file in an answer.

    A fetched artifact lives at a temp path, and naming that path in a result tells the reader
    about our filesystem instead of about their board: "200 findings on tmpcqaiq9zy.kicad_pcb"
    is the reference they gave, laundered into noise.
    """
    ref = str(reference).strip()
    parsed = urllib.parse.urlparse(ref)
    tail = (parsed.netloc + parsed.path) if parsed.scheme else ref
    return Path(urllib.parse.unquote(tail)).name or ref


@contextmanager
def resolved(reference: str, prefix: str, what: str = "file"):
    """Yield a local path for `reference`, fetching it first if it is remote.

    `prefix` names the server's env vars (FARADAY, HERTZ, KIRCHHOFF). A fetched file is
    removed when the block exits; a local one is left exactly where it was.
    """
    if not reference or not str(reference).strip():
        raise ValueError(f"no {what} given")
    ref = str(reference).strip()
    parsed = urllib.parse.urlparse(ref)

    if parsed.scheme in ("", "file"):
        path = Path(urllib.parse.unquote(parsed.path) if parsed.scheme == "file" else ref)
        path = path.expanduser()
        if not path.exists():
            raise ValueError(
                f"no {what} at {path} — a bare path is read from the machine running this "
                f"server, so it must be local to it. For a file held by the orchestrator, "
                f"pass artifact://<id> or an https:// URL instead.")
        yield path
        return

    if parsed.scheme == "artifact":
        base = _env(prefix, "ARTIFACT_BASE")
        if not base:
            raise ValueError(
                f"cannot resolve {ref}: set {prefix}_ARTIFACT_BASE to the orchestrator's "
                f"artifact endpoint (e.g. http://127.0.0.1:8404/artifacts), or pass a local "
                f"path instead")
        ident = (parsed.netloc + parsed.path).strip("/")
        url = f"{base.rstrip('/')}/{ident}"
    elif parsed.scheme in ("http", "https"):
        url = ref
    else:
        raise ValueError(
            f"unsupported reference scheme {parsed.scheme!r} in {ref} — use a local path, "
            f"file://, artifact://<id> or https://")

    request = urllib.request.Request(url)
    token = _env(prefix, "ARTIFACT_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    suffix = Path(urllib.parse.urlparse(url).path).suffix or ""
    handle = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
    # The FETCH is what this try guards. The caller's own work happens after it, below, and
    # must not be caught here — reporting a solver's exception as "could not fetch" would send
    # the reader to the network for a problem in the engine.
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            size = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FETCH_BYTES:
                    raise ValueError(
                        f"{ref} exceeds the {MAX_FETCH_BYTES // (1024 * 1024)} MB fetch limit")
                handle.write(chunk)
        handle.close()
    except Exception as error:                                   # noqa: BLE001
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        # The URL is named, the token never is.
        raise ValueError(
            f"could not fetch {what} {ref}: {type(error).__name__}: {error}") from error

    try:
        yield Path(handle.name)
    finally:
        Path(handle.name).unlink(missing_ok=True)
