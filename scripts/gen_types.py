#!/usr/bin/env python3
"""Generate Python classes from MAS / PEAS / SAS / CAS / RAS schemas.

Uses `quicktype` to convert each JSON schema into a single Python module
under ``heaviside/types/_generated/``. Each module carries plain classes
with ``from_dict`` / ``to_dict`` converters, so boundary signatures can
read ``def f(mas: Magnetic)`` and payloads can be validated loudly with
``Magnetic.from_dict(payload)`` (asserts on shape mismatch — no silent
fallbacks).

The generated tree is **not** checked in (``heaviside/types/_generated/``
is gitignored). It is regenerated from the schema submodules:

* locally via ``make types`` (a dependency of ``make type`` / ``make ci``),
* in CI before the mypy and unit-test steps.

A content stamp (``.stamp.json``, sha256 over every source schema plus the
generator config) makes reruns instant no-ops while the schemas are
unchanged; bumping a schema submodule invalidates the stamp and the next
run regenerates. ``--force`` regenerates unconditionally.

Cross-repo ``$ref``s in the schema repos use the canonical
``https://psma.com/<repo>/<path>`` URIs (the same URIs the librarian's
``referencing.Registry`` validates against); intra-repo refs are
file-relative. quicktype cannot resolve the psma.com URIs offline, so each
top-level schema is **bundled** in memory — every transitively referenced
schema file is inlined under ``$defs/__bundled__/<key>`` with all refs
rewritten to JSON-pointer fragments — and the self-contained result is
piped to quicktype on stdin. No network, no temp files, no repo mirror;
everything stays pinned to the submodule SHAs.

Per Heaviside design rules: never edit the generated files by hand. Edit
the schema in the submodule, push upstream, bump the submodule pin,
regenerate.

We deliberately do **not** generate Pydantic models. The project keeps a
hard cap of 8 BaseModel classes (enforced by CI); quicktype's plain-class
output does not count against that cap.

Layout written:

    heaviside/types/_generated/
        topologies/<schema>.py     — one module per MAS topology schema
        mas/<schema>.py            — MAS top-levels (mas, magnetic, inputs,
                                     outputs) + components (core, coil, wire, …)
        peas/<schema>.py           — PEAS top-level
        cas/<schema>.py            — capacitors
        ras/<schema>.py            — resistors
        sas/<schema>.py            — semiconductors
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "heaviside" / "types" / "_generated"
STAMP = OUT / ".stamp.json"

#: Canonical cross-repo URI prefix. ``https://psma.com/<repo>/<path>``
#: maps onto the matching schema submodule below — resolution is fully
#: local and pinned to the submodule SHAs.
PSMA_PREFIX = "https://psma.com/"
REPO_MAP: dict[str, Path] = {
    "mas": ROOT / "MAS" / "schemas",
    "peas": ROOT / "PEAS" / "schemas",
    "cas": ROOT / "CAS" / "schemas",
    "ras": ROOT / "RAS" / "schemas",
    "sas": ROOT / "SAS" / "schemas",
    # PEAS family consolidation (PSMA main) split controller -> CTAS and the
    # analog parts -> AAS, and PEAS now $refs them; the rest are present so any
    # cross-repo $ref resolves locally. Only the SOURCES below generate types;
    # these extra entries exist purely for offline $ref resolution.
    "ctas": ROOT / "CTAS" / "schemas",
    "aas": ROOT / "AAS" / "schemas",
    "conas": ROOT / "CONAS" / "schemas",
    "tas": ROOT / "TAS" / "schemas",
    "cias": ROOT / "CIAS" / "schemas",
}

#: Bump when the quicktype invocation or bundling changes, so existing
#: checkouts regenerate even though the schemas themselves are unchanged.
GENERATOR_VERSION = 6

# Source schema roots. Each entry: (repo key in REPO_MAP, subdir, output
# bucket). Directories are scanned non-recursively (top-level *.json only).
SOURCES: list[tuple[str, str, str]] = [
    ("mas", "inputs/topologies", "topologies"),
    ("mas", "", "mas"),
    ("mas", "magnetic", "mas"),
    ("peas", "", "peas"),
    ("cas", "", "cas"),
    ("ras", "", "ras"),
    ("sas", "", "sas"),
]

#: Schemas that are pure ``$defs`` collections with no useful top-level
#: type of their own — they are pulled in by reference from the others.
SKIP_STEMS: frozenset[str] = frozenset({"utils"})


def _quicktype_cmd() -> list[str]:
    """Prefer a globally installed quicktype; fall back to npx."""
    exe = shutil.which("quicktype")
    if exe is not None:
        return [exe]
    if shutil.which("npx") is not None:
        return ["npx", "--yes", "quicktype"]
    print(
        "ERROR: neither `quicktype` nor `npx` found on PATH. "
        "Install Node.js >= 18 and `npm install -g quicktype`.",
        file=sys.stderr,
    )
    sys.exit(2)


def _module_name(stem: str) -> str:
    """Schema filename stem → generated module name (snake_case)."""
    s = stem.replace("-", "_")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return s.lower()


def _top_level_name(stem: str) -> str:
    """Schema filename stem → top-level class name (PascalCase)."""
    parts = re.split(r"[-_]", stem)
    out = []
    for part in parts:
        if part.isupper():  # acronym files like MAS.json, CAS.json
            out.append(part.capitalize())
        else:
            out.append(part[:1].upper() + part[1:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Schema bundling (self-contained document for quicktype)
# ---------------------------------------------------------------------------


def _locate(file: Path) -> tuple[str, str]:
    """Return ``(repo key, repo-relative posix path)`` for a schema file."""
    resolved = file.resolve()
    for repo, root in REPO_MAP.items():
        try:
            rel = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return repo, rel.as_posix()
    raise SystemExit(f"schema {file} lies outside every known schema repo")


def _bundle_key(file: Path) -> str:
    repo, rel = _locate(file)
    stem = rel[:-5] if rel.endswith(".json") else rel
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{repo}_{stem}")


def _resolve_ref(base: Path, ref: str) -> tuple[Path | None, str]:
    """Resolve a ``$ref`` to ``(target file or None-if-internal, fragment)``.

    Handles the two conventions the repos use: canonical psma.com URIs
    for cross-repo refs and file-relative paths for intra-repo refs.
    """
    path_part, _, frag = ref.partition("#")
    if frag and not frag.startswith("/"):
        raise SystemExit(f"{base}: $ref {ref!r} uses a non-pointer anchor fragment")
    if not path_part:
        return None, frag
    if path_part.startswith(PSMA_PREFIX):
        repo, _, rest = path_part[len(PSMA_PREFIX) :].partition("/")
        if repo not in REPO_MAP:
            raise SystemExit(f"{base}: $ref {ref!r} names unknown psma repo {repo!r}")
        target = REPO_MAP[repo] / rest
    elif path_part.startswith(("http://", "https://")):
        raise SystemExit(f"{base}: $ref {ref!r} is a non-psma URL — refusing to fetch")
    else:
        target = base.parent / path_part
    target = target.resolve()
    if not target.is_file():
        raise SystemExit(f"{base}: $ref {ref!r} resolves to missing file {target}")
    return target, frag


class _Bundler:
    """Inline every transitively-referenced schema file into the root.

    Each referenced file becomes ``$defs/__bundled__/<key>`` (its own
    ``$defs`` ride along inside it); every cross-file ``$ref`` is
    rewritten to a plain JSON-pointer fragment. Cycles between files are
    fine — files embed once, refs become pointers.
    """

    def __init__(self, root_file: Path) -> None:
        self.root_file = root_file.resolve()
        self.bundled: dict[str, Any] = {}
        self._scheduled: set[str] = set()

    def bundle(self) -> dict[str, Any]:
        doc = json.loads(self.root_file.read_text(encoding="utf-8"))
        out = self._rewrite(doc, self.root_file, None)
        if self.bundled:
            out.setdefault("$defs", {})["__bundled__"] = self.bundled
        return out

    def _embed(self, file: Path) -> str:
        key = _bundle_key(file)
        if key in self._scheduled:
            return key
        self._scheduled.add(key)
        doc = json.loads(file.read_text(encoding="utf-8"))
        self.bundled[key] = self._rewrite(doc, file, key)
        return key

    def _rewrite(self, node: Any, base: Path, self_key: str | None) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "$id" or (k == "$schema" and self_key is not None):
                    continue
                if k == "$ref" and isinstance(v, str):
                    target, frag = _resolve_ref(base, v)
                    if target is None:  # internal ref
                        out[k] = f"#/$defs/__bundled__/{self_key}{frag}" if self_key else f"#{frag}"
                    elif target == self.root_file:
                        out[k] = f"#{frag}"
                    else:
                        out[k] = f"#/$defs/__bundled__/{self._embed(target)}{frag}"
                else:
                    out[k] = self._rewrite(v, base, self_key)
            return out
        if isinstance(node, list):
            return [self._rewrite(v, base, self_key) for v in node]
        return node


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _normalise_top_level(out_file: Path, desired: str) -> None:
    """Undo quicktype's forced acronym styling on the top-level class.

    quicktype's Python target has a built-in acronym list ("sas", "ras",
    …) that uppercases matching class names regardless of the schema
    title, and exposes no ``--acronym-style`` knob for Python. Rename
    the emitted ``class SAS`` back to the canonical ``Sas``.
    """
    upper = desired.upper()
    if desired == upper:
        return
    src = out_file.read_text(encoding="utf-8")
    has_upper = re.search(rf"^class {re.escape(upper)}\b", src, re.M)
    has_desired = re.search(rf"^class {re.escape(desired)}\b", src, re.M)
    if has_upper and not has_desired:
        out_file.write_text(re.sub(rf"\b{re.escape(upper)}\b", desired, src), encoding="utf-8")


_FIELD_DECL_RE = re.compile(r"^    ([A-Za-z_]\w*): (.+)$")


def _fix_dataclass_field_order(out_file: Path) -> None:
    """Append ``= None`` to any dataclass field with no default that follows a
    defaulted one.

    quicktype renders an open-schema property (``"temperature": {}`` in CAS's
    ``thermal`` allOf-extension) as a required ``Any`` field, and can emit it
    *after* the optional fields — which is invalid Python (``non-default
    argument follows default argument``). Defaulting the trailing field (rather
    than reordering) keeps quicktype's positional ``from_dict`` constructor
    calls correct, since the value is still passed explicitly there.
    """
    lines = out_file.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_dataclass = False
    seen_default = False
    changed = False
    for line in lines:
        if line.startswith("@dataclass"):
            in_dataclass, seen_default = True, False
            out.append(line)
            continue
        if in_dataclass:
            if line.startswith("class "):
                # The ``class X:`` header right after @dataclass — stay in the
                # field section (it is non-indented but not the end).
                out.append(line)
                continue
            # A method/decorator or any dedented (module-level) line ends the
            # field section; blank lines and field docstrings do not.
            if line.startswith(("    def ", "    @")) or (line and not line.startswith(" ")):
                in_dataclass = False
            else:
                m = _FIELD_DECL_RE.match(line)
                if m:
                    if " = " in m.group(2):
                        seen_default = True
                    elif seen_default:
                        out.append(line + " = None")
                        changed = True
                        continue
        out.append(line)
    if changed:
        out_file.write_text("\n".join(out) + "\n", encoding="utf-8")


def _gen_one(quicktype: list[str], schema: Path, out_dir: Path) -> None:
    top_level = _top_level_name(schema.stem)
    bundle = _Bundler(schema).bundle()
    # quicktype prefers the schema title over --top-level; the bundle is
    # ours, so pin the title to the canonical class name.
    bundle["title"] = top_level

    out_file = out_dir / f"{_module_name(schema.stem)}.py"
    cmd = [
        *quicktype,
        "--src-lang",
        "schema",
        "--lang",
        "python",
        "--python-version",
        "3.7",
        "--top-level",
        top_level,
        "-o",
        str(out_file),
    ]
    res = subprocess.run(cmd, input=json.dumps(bundle), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAIL {schema.name}:\n{res.stderr}", file=sys.stderr)
        raise SystemExit(res.returncode)
    _normalise_top_level(out_file, top_level)
    _fix_dataclass_field_order(out_file)


def _iter_schemas() -> list[tuple[Path, str]]:
    """All (schema file, bucket) pairs under the source roots."""
    pairs: list[tuple[Path, str]] = []
    for repo, subdir, bucket in SOURCES:
        src = REPO_MAP[repo] / subdir if subdir else REPO_MAP[repo]
        for schema in sorted(src.glob("*.json")):
            if schema.stem in SKIP_STEMS:
                continue
            pairs.append((schema, bucket))
    return pairs


def _fingerprint() -> str:
    """sha256 over generator config + every source schema's bytes.

    Hashes *all* schema files in the source submodules (not just the
    top-level ones we feed to quicktype) because the bundler resolves
    cross-file ``$ref``s — a change in a ref'd file must invalidate the
    stamp too.
    """
    h = hashlib.sha256()
    h.update(f"generator-version={GENERATOR_VERSION}".encode())
    for repo in sorted(REPO_MAP):
        for f in sorted(REPO_MAP[repo].rglob("*.json")):
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _is_fresh(fingerprint: str) -> bool:
    if not STAMP.exists():
        return False
    try:
        stamp = json.loads(STAMP.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return stamp.get("fingerprint") == fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Only verify schema directories exist."
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if the stamp is fresh."
    )
    args = parser.parse_args()

    missing = [
        REPO_MAP[repo] / subdir
        for repo, subdir, _ in SOURCES
        if not (REPO_MAP[repo] / subdir).exists()
    ]
    if missing:
        print("Missing schema sources (submodules not initialised?):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 1

    if args.check:
        print("All schema source directories present:")
        for repo, subdir, _ in SOURCES:
            print(f"  - {(REPO_MAP[repo] / subdir).relative_to(ROOT)}")
        return 0

    fingerprint = _fingerprint()
    if not args.force and _is_fresh(fingerprint):
        print(f"types up to date (stamp {fingerprint[:12]}…) — nothing to do")
        return 0

    quicktype = _quicktype_cmd()
    pairs = _iter_schemas()

    # Rebuild the output tree from scratch — stale modules from removed
    # schemas must not linger.
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / "__init__.py").write_text(
        '"""Generated schema classes. Do not edit; run `make types`."""\n'
    )

    total = 0
    for schema, bucket in pairs:
        bucket_dir = OUT / bucket
        if not bucket_dir.exists():
            bucket_dir.mkdir(parents=True)
            (bucket_dir / "__init__.py").write_text(f'"""Generated schema classes: {bucket}."""\n')
        _gen_one(quicktype, schema, bucket_dir)
        total += 1
        print(f"  {bucket}/{_module_name(schema.stem)}  (top-level {_top_level_name(schema.stem)})")

    STAMP.write_text(json.dumps({"fingerprint": fingerprint, "modules": total}, indent=2) + "\n")
    print(f"\nGenerated {total} modules under {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
