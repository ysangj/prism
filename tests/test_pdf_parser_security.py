"""Phase 2 security: static inspection of pdf_parser.py (PRD §9, §15.6).

PRD §9 requires the BYOK Anthropic key to be (a) never read from the
environment/config/disk and (b) never persisted or logged; and the PDF /
extracted term-sheet data must not be persisted beyond the session.

These are *source-level* guards: they fail if a future edit reintroduces an
env-var read, a file write, or a logging statement in pdf_parser.py.
"""
from __future__ import annotations

import ast
import pathlib

import prism.pdf_parser as pp

SRC_PATH = pathlib.Path(pp.__file__)
SRC = SRC_PATH.read_text()
TREE = ast.parse(SRC)


def _all_attr_chains():
    """Yield dotted-name strings for every Attribute access in the module."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                yield ".".join(reversed(parts))


CHAINS = list(_all_attr_chains())
IMPORTS = {
    n.names[0].name if isinstance(n, ast.Import) else n.module
    for n in ast.walk(TREE)
    if isinstance(n, (ast.Import, ast.ImportFrom))
}


def test_no_os_environ_or_getenv():
    """The key must never be read from the environment (PRD §9).

    Inspects executable code via the AST (string literals such as the docstring,
    which legitimately mentions "environment", are ignored). Fails if any name or
    attribute chain references os / environ / getenv.
    """
    assert "os.environ" not in SRC
    assert "os.getenv" not in SRC
    # `os` must not be imported by the parser at all (no path to env access).
    assert "os" not in {i for i in IMPORTS if i}

    # No identifier in executable code references env access.
    forbidden = {"os", "environ", "getenv"}
    for node in ast.walk(TREE):
        if isinstance(node, ast.Name):
            assert node.id not in forbidden, f"env access via name {node.id!r}"
        if isinstance(node, ast.Attribute):
            assert node.attr not in forbidden, f"env access via attr {node.attr!r}"
    # And no env access appears in any dotted chain we collected.
    for chain in CHAINS:
        assert "environ" not in chain and "getenv" not in chain


def test_no_dotenv_or_config_read():
    assert "dotenv" not in SRC.lower()
    assert "load_dotenv" not in SRC


def test_no_file_writes():
    """Neither the key, the PDF, nor extracted data may be persisted (PRD §9)."""
    # No `open(...)` calls.
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", "pdf_parser must not open files"
    for bad in ("open(", "pathlib", "Path(", ".write(", ".write_text",
                ".write_bytes", "pickle", "shelve", "tempfile", "NamedTemporary"):
        assert bad not in SRC, f"unexpected persistence primitive: {bad!r}"


def test_no_logging():
    """The key/PDF must never be logged (PRD §9)."""
    assert "logging" not in IMPORTS
    assert "import logging" not in SRC
    assert "logger" not in SRC.lower()
    # `print(` is also a leak channel.
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", "pdf_parser must not print"


def test_parse_term_sheet_takes_key_as_argument():
    """The contract: key is a parameter, not a module-level lookup."""
    fn = next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "parse_term_sheet")
    arg_names = [a.arg for a in fn.args.args]
    assert "api_key" in arg_names, "api_key must be an explicit parameter"
    assert "pdf_bytes" in arg_names


def test_anthropic_client_built_with_passed_key():
    """The Anthropic client must be constructed with api_key=<the arg>."""
    found = False
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            kwnames = {kw.arg for kw in node.keywords}
            if "api_key" in kwnames:
                found = True
    assert found, "Anthropic(...) should be built with api_key=<arg>"
