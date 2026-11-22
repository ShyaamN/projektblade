"""Select observed callers of a verified, local Python source change."""

from __future__ import annotations

import ast
import base64
import gzip
import marshal
from dataclasses import dataclass
from pathlib import Path
import re
import struct


_FILE = re.compile(r"diff --git a/(\S+) b/(\S+)\Z")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PLAIN_COMMENT = re.compile(r"\s*# [A-Za-z][A-Za-z ,.-]*\Z")
_DIRECTIVE = re.compile(r"(?i)noqa|type|coding|pragma|fmt|lint|pylint|ruff|pyright|mypy")
_ENUM = re.compile(
    r"(?P<left>\bself\.normalization_strategy\s+)(?P<op>is not|is|==|!=)"
    r"(?P<right>\s+NormalizationStrategy\.[A-Z_]+\b)"
)
_VERSION = re.compile(
    r"(?P<left>\bself\.dialect\.version\s+)(?P<op><=|>=|<|>)"
    r"(?P<right>\s+\(\d+,\s*\d+\))"
)


def _patched_source(path: str, source: str, patch: str) -> str | None:
    """Apply the named file's hunks only when their old text and counts match."""
    lines = source.splitlines()
    output = []
    current = False
    count = 0
    cursor = 0
    saw_hunk = False
    in_hunk = False
    old_left = new_left = 0
    for line in patch.splitlines():
        if header := _FILE.fullmatch(line):
            if current and (old_left or new_left):
                return None
            current = header[1] == header[2] == path
            in_hunk = False
            if current:
                count += 1
            continue
        if not current:
            continue
        if header := _HUNK.match(line):
            if old_left or new_left:
                return None
            old_left = int(header[2] or "1")
            new_left = int(header[4] or "1")
            start = int(header[1]) - bool(old_left)
            if not cursor <= start <= len(lines):
                return None
            output.extend(lines[cursor:start])
            cursor = start
            in_hunk = saw_hunk = True
        elif in_hunk:
            if line.startswith("\\"):
                continue
            if not line or line[0] not in " +-":
                return None
            if line[0] != "+":
                if old_left <= 0 or cursor >= len(lines) or lines[cursor] != line[1:]:
                    return None
                old_left -= 1
                cursor += 1
            if line[0] != "-":
                if new_left <= 0:
                    return None
                new_left -= 1
                output.append(line[1:])
        elif line.startswith(("old mode ", "new mode ", "rename ", "copy ")):
            return None
    if count != 1 or not saw_hunk or old_left or new_left:
        return None
    return "\n".join([*output, *lines[cursor:]]) + "\n"


def python_risk(patch: str, changes, sources: dict) -> str | None:
    """Full-suite reason when a Python edit cannot be reconstructed and compiled.

    ``compile`` does not execute the file. A rename, deletion, mismatched hunk,
    or syntax error can fail import or collection for every exposed test.
    """
    for change in changes:
        old, new = change.old_path, change.new_path
        if not any(path and path.endswith(".py") for path in (old, new)):
            continue
        if old != new:
            return "python_path_changed"
        if old not in sources:
            return "python_source_missing"
        output = _patched_source(old, sources[old], patch)
        if output is None:
            return "python_context"
        try:
            compile(output, old, "exec")
        except (SyntaxError, ValueError):
            return "python_syntax"
    return None


def _ast(source: str) -> ast.Module:
    return ast.parse(source, type_comments=True)


def _same(left, right) -> bool:
    """Compare syntax, ignoring source locations."""
    if type(left) is not type(right):
        return False
    if isinstance(left, ast.AST):
        for field in left._fields:
            if not _same(getattr(left, field, None), getattr(right, field, None)):
                return False
        return True
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _nodes_at(tree: ast.AST, line: int):
    if line < getattr(tree, "lineno", line) or line > (getattr(tree, "end_lineno", None) or line):
        return
    yield tree
    for child in ast.iter_child_nodes(tree):
        yield from _nodes_at(child, line)


def _local_statement(tree: ast.AST, line: int) -> ast.stmt | None:
    for fn in _nodes_at(tree, line):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.lineno < line <= fn.end_lineno:
            continue
        for stmt in _nodes_at(fn, line):
            if isinstance(stmt, (ast.If, ast.Assign)) and stmt.lineno == line:
                return stmt
    return None


def _safe_expression(node: ast.expr) -> bool:
    # These forms can change a function's locals or calling convention even
    # when no invocation reached the original statement.
    return not any(isinstance(n, (ast.NamedExpr, ast.Yield, ast.YieldFrom, ast.Await)) for n in ast.walk(node))


def _guard_bindings(node: ast.expr) -> tuple | None:
    bindings = []
    for part in ast.walk(node):
        if isinstance(part, (ast.Yield, ast.YieldFrom, ast.Await)):
            return None
        if isinstance(part, ast.NamedExpr):
            bindings.append((part.lineno, part.col_offset, ast.dump(part)))
    if bindings and any(
        isinstance(p, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        for p in ast.walk(node)
    ):
        return None
    return tuple(sorted(bindings))


def classify_line(path: str, original: str, line: int, replacement: str) -> str | None:
    """Return exact-line eligibility for a local condition or assignment value."""
    lines = original.splitlines()
    before = lines[line - 1]
    if len(before) - len(before.lstrip()) != len(replacement) - len(replacement.lstrip()):
        return None
    lines[line - 1] = replacement
    try:
        left, right = _ast(original), _ast("\n".join(lines))
    except SyntaxError:
        return None
    old, new = _local_statement(left, line), _local_statement(right, line)
    if old is None or type(old) is not type(new):
        return None
    if isinstance(old, ast.If):
        old_value, new_value = old.test, new.test
        old.test = new.test = ast.Constant(value=None)
    else:
        if not all(isinstance(t, ast.Name) for t in old.targets):
            return None
        old_value, new_value = old.value, new.value
        old.value = new.value = ast.Constant(value=None)
    if any(v.end_lineno != line or not _safe_expression(v) for v in (old_value, new_value)):
        return None
    if not _same(left, right):
        return None
    return "exact"


def _same_operator(path: str, before: str, after: str) -> bool:
    # Pinned Dialect supplies enum members and the pinned overrides use enum
    # members too. Its version is always a triple, never equal to a pair.
    if path == "sqlglot/dialects/dialect.py":
        pattern = _ENUM
        equivalents = {"is": "==", "==": "==", "is not": "!=", "!=": "!="}
    elif path.startswith("sqlglot/generators/"):
        pattern = _VERSION
        equivalents = {"<": "<", "<=": "<", ">": ">", ">=": ">"}
    else:
        return False
    # Match a real conditional in a validated one-line replacement. Normalize
    # the operator only, and reject any other differences including indentation.
    before_test = before.lstrip().split(" ", 1)
    after_test = after.lstrip().split(" ", 1)
    if len(before_test) != 2 or len(after_test) != 2 or before_test[0] not in {"if", "elif"} or after_test[0] != before_test[0]:
        return False
    a = pattern.match(before, len(before) - len(before_test[1]))
    b = pattern.match(after, len(after) - len(after_test[1]))
    if a is None or b is None or a["op"] == b["op"]:
        return False
    normalized = lambda line, m: line[:m.start("op")] + equivalents[m["op"]] + line[m.end("op"):]
    return normalized(before, a) == normalized(after, b)


def _detached_copy_equivalent(path: str, original: str, updated: str, line: int) -> bool:
    # _new_cte returns a string name. table_(name) allocates a fresh Table
    # with a new Identifier, which has no external owner before alias_ changes it.
    if path != "sqlglot/optimizer/eliminate_subqueries.py":
        return False
    left, right = _ast(original), _ast(updated)
    toggles = []
    for tree in (left, right):
        stmt = _local_statement(tree, line)
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
            return False
        call = stmt.value
        if ast.unparse(call.func) != "exp.alias_" or [ast.unparse(a) for a in call.args] != ["exp.table_(name)"]:
            return False
        toggled = next((x for x in call.keywords if x.arg == "copy"), None)
        if toggled is None or not isinstance(toggled.value, ast.Constant) or type(toggled.value.value) is not bool:
            return False
        toggles.append(toggled.value.value)
        toggled.value = ast.Constant(value=False)
    return toggles[0] != toggles[1] and _same(left, right)


def _predicate_entry(original: str, updated: str, line: int) -> int | None:
    left, right = _ast(original), _ast(updated)
    starts = []
    scopes = []
    for tree in (left, right):
        candidates = [
            stmt
            for fn in _nodes_at(tree, line) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for stmt in _nodes_at(fn, line)
            if isinstance(stmt, ast.If) and stmt.test.lineno <= line <= stmt.test.end_lineno
        ]
        if not candidates:
            return None
        stmt = min(candidates, key=lambda item: item.test.end_lineno - item.test.lineno)
        bindings = _guard_bindings(stmt.test)
        if bindings is None or isinstance(stmt.test, ast.Constant):
            return None
        scopes.append(bindings)
        starts.append(stmt.test.lineno)
        stmt.test = ast.Constant(value=None)
    return starts[0] if starts[0] == starts[1] and scopes[0] == scopes[1] and _same(left, right) else None


def _escape_keys(path: str, original: str, updated: str, line: int) -> set[str] | None:
    if path != "sqlglot/dialects/snowflake.py":
        return None
    left, right = _ast(original), _ast(updated)
    values = []
    for tree in (left, right):
        for node in _nodes_at(tree, line):
            if (
                isinstance(node, ast.Assign) and node.lineno == node.end_lineno == line
                and len(node.targets) == 1 and ast.unparse(node.targets[0]) == "NUMERIC_ESCAPES"
                and isinstance(node.value, ast.Dict)
            ):
                values.append(ast.literal_eval(node.value))
                node.value = ast.Constant(value=None)
                break
    if len(values) != 2 or not _same(left, right):
        return None
    before, after = values
    if before.keys() != after.keys() or any(
        type(key) is not str or type(spec) is not tuple or len(spec) != 4
        or any(type(part) is not int for part in spec)
        for mapping in values for key, spec in mapping.items()
    ):
        return None
    return {key for key, spec in before.items() if after[key] != spec}


@dataclass
class Index:
    sources: dict
    lines: dict
    preserve: set[int]
    tokenizer: dict | None
    primary: dict | None = None
    operator_lines: dict | None = None

    @classmethod
    def load(cls, path: Path, ids: dict[str, int], sha: str) -> Index | None:
        try:
            with gzip.open(path, "rb") as stream:
                data = marshal.loads(stream.read(), allow_code=False)
            if data["schema"] != 3 or data["pinned_sha"] != sha or data["test_ids"] != list(ids):
                return None
            return cls(data["sources"], data["lines"], set(map(ids.__getitem__, data["preserve"])),
                       data.get("tokenizer"), data.get("primary"), data.get("operator_lines"))
        except (OSError, EOFError, ValueError, KeyError, TypeError):
            return None

    def _hits(self, path: str, line: int) -> set[int]:
        posting = self.lines[path].get(str(line))
        if posting is None:
            return set()
        return {n for n, in struct.iter_unpack("<H", base64.b64decode(posting))}

    def narrow(self, patch, change, *, allow_equivalence: bool = True) -> set[int] | None:
        path = change.old_path
        if path != change.new_path or path not in self.sources:
            return None
        source = self.sources[path]
        output = _patched_source(path, source, patch)
        if output is None:
            return None
        bodies = change.added_bodies + change.removed_bodies
        if bodies and all(_PLAIN_COMMENT.fullmatch(s) and not _DIRECTIVE.search(s) for s in bodies):
            try:
                if _same(_ast(source), _ast(output)):
                    return set()
            except SyntaxError:
                return None
        if (
            not change.structural
            and len(change.old_lines) == len(change.added_bodies) == len(change.removed_bodies) == 1
            and path in self.lines
        ):
            line, = change.old_lines
            try:
                if allow_equivalence and (
                    line in (self.operator_lines or {}).get(path, ())
                    and _same_operator(path, change.removed_bodies[0], change.added_bodies[0])
                    or _detached_copy_equivalent(path, source, output, line)
                ):
                    return set()
                if allow_equivalence and self.tokenizer is not None:
                    keys = _escape_keys(path, source, output, line)
                    if keys is not None:
                        # Class initialization and escaping output inspect only
                        # key presence. Decoder value reads are indexed by key.
                        return set(self.tokenizer["unobserved"]) | {
                            n for key in keys for n in self.tokenizer["keys"].get(key, ())
                        }
                entry = _predicate_entry(source, output, line)
            except (SyntaxError, ValueError, TypeError):
                return None
            if entry is not None:
                return self._hits(path, entry)
            if classify_line(path, source, line, change.added_bodies[0]) == "exact":
                return self._hits(path, line)
        return None
