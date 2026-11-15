"""Compact MPN -> byte-offset index for one TAS NDJSON file.

``/catalog/{category}/{mpn}/detail`` used to scan the whole file for every
request: a part near the end of the 2.9 GB resistors file took ~60 s, every
time. This index holds, per part, only an 8-byte hash of its lower-cased MPN
and the byte offset of its line (two ``array('Q')`` columns, ~16 bytes a part,
about 21 MB for 1.3 M resistors), sorted by hash. A lookup bisects the hash
column and reads just the candidate lines, confirming each against the MPN, so
a hash collision can never return the wrong part.

The index records the file's (size, mtime_ns) when built; a lookup on a file
that changed since raises :class:`IndexStale` and the caller rebuilds. A read
error during the build propagates — a partial index is never kept.
"""

from __future__ import annotations

import hashlib
import json
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from heaviside.catalogue._reader import iter_envelopes_at

KeyFn = Callable[[dict[str, Any]], Iterator[str]]


class IndexStale(RuntimeError):
    """The NDJSON file changed after the index was built."""


def _h(key: str) -> int:
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "little")


def _stamp(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


class MpnOffsetIndex:
    def __init__(self, path: Path, keys_of: KeyFn) -> None:
        """Index every line of ``path`` under each lower-cased key ``keys_of(env)`` yields."""
        self.path = path
        self._keys_of = keys_of
        stamp = _stamp(path)
        hashes = array("Q")
        offsets = array("Q")
        for offset, env in iter_envelopes_at(path):
            for key in {k.lower() for k in keys_of(env)}:  # one entry per distinct key per line
                hashes.append(_h(key))
                offsets.append(offset)
        if _stamp(path) != stamp:
            raise IndexStale(f"{path} was rewritten while it was being indexed")
        # Sort through a permutation, not a list of (hash, offset) tuples: the
        # tuples would cost ~100 bytes a part at build time on a RAM-tight host.
        order = sorted(range(len(hashes)), key=hashes.__getitem__)
        self._hashes = array("Q", (hashes[i] for i in order))
        self._offsets = array("Q", (offsets[i] for i in order))
        del order, hashes, offsets
        self._stamp = stamp

    def __len__(self) -> int:
        return len(self._hashes)

    def is_stale(self) -> bool:
        """True when the file changed (size or mtime) since the index was built."""
        return _stamp(self.path) != self._stamp

    def lookup(self, mpn: str) -> dict[str, Any] | None:
        """The first envelope (in file order) whose key equals ``mpn``, case-insensitively."""
        if _stamp(self.path) != self._stamp:
            raise IndexStale(f"{self.path} changed since it was indexed")
        want = mpn.strip().lower()
        h = _h(want)
        i = bisect_left(self._hashes, h)
        offsets: list[int] = []
        while i < len(self._hashes) and self._hashes[i] == h:
            offsets.append(self._offsets[i])
            i += 1
        if not offsets:
            return None
        with self.path.open("rb") as fh:
            for off in sorted(offsets):
                fh.seek(off)
                env = json.loads(fh.readline())
                if any(k.lower() == want for k in self._keys_of(env)):
                    return env
        return None


__all__ = ["IndexStale", "MpnOffsetIndex"]
