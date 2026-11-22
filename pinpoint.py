"""Select observed callers of a verified, local Python source change."""

from __future__ import annotations

import ast
import base64
import builtins
import gzip
from io import BytesIO
import keyword
import marshal
from dataclasses import dataclass
from pathlib import Path
import re
import struct
import tokenize
import zlib


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
_EXPRESSION_FLAG = re.compile(r"(\s*['\"][^'\"]+['\"]:\s*)(True|False)(,\s*)")
_SHOW_FLAG = re.compile(
    r"(\s*['\"][A-Z0-9_ ]+['\"]:\s*_show_parser\(['\"][A-Z0-9_ ]+['\"],\s*target=)"
    r"(True|False)(\),\s*)"
)


def unpack_lines(value: bytes) -> dict:
    return marshal.loads(zlib.decompress(value), allow_code=False)


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


def _terminal_assertion_change(old: list[ast.stmt], new: list[ast.stmt]) -> bool:
    if len(new) == len(old) + 1 and _same(old, new[:-1]):
        last = new[-1]
        return (isinstance(last, ast.Assert) and isinstance(last.test, ast.Constant)
                and type(last.test.value) is bool
                and (last.msg is None or isinstance(last.msg, ast.Constant)))
    if not old or len(new) != len(old) or not _same(old[:-1], new[:-1]):
        return False
    calls = [stmt.value for stmt in (old[-1], new[-1]) if isinstance(stmt, ast.Expr)]
    if len(calls) != 2 or not all(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
        and call.func.attr in {"assertEqual", "assertNotEqual", "assertIs", "assertIsNot"}
        and len(call.args) == 2 and not call.keywords and isinstance(call.args[1], ast.Constant)
        for call in calls
    ):
        return False
    return _same(calls[0].func, calls[1].func) and _same(calls[0].args[0], calls[1].args[0])


def _test_body_changes(before: str, after: str) -> tuple[set[str], bool] | None:
    trees = [_ast(before), _ast(after)]
    bodies = [{}, {}]
    for tree, functions in zip(trees, bodies):
        scopes = [(node, ()) for node in tree.body]
        while scopes:
            node, scope = scopes.pop()
            if isinstance(node, ast.ClassDef):
                scopes.extend((stmt, (*scope, node.name)) for stmt in node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                if node.decorator_list:
                    continue
                name = "::".join((*scope, node.name))
                if name in functions:
                    return None
                functions[name] = node.body
                node.body = []
    if not _same(*trees) or bodies[0].keys() != bodies[1].keys():
        return None
    changed = {name for name in bodies[0] if not _same(bodies[0][name], bodies[1][name])}
    return changed, all(_terminal_assertion_change(bodies[0][name], bodies[1][name]) for name in changed)


def _eager_nodes(tree: ast.AST):
    pending = [tree]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pending.extend(reversed(node.decorator_list))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            pending.extend(arg for arg in node.args.defaults + node.args.kw_defaults if arg is not None)
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _import_time_operations(source: str) -> list[str]:
    return [ast.dump(node) for node in _eager_nodes(_ast(source))
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Raise))]


def _only_unused_redundant_imports(before, after, output, sites, modified) -> bool:
    if not sites or len(before) <= len(after):
        return False
    next_operation = 0
    removed = []
    for old in before:
        if next_operation < len(after) and after[next_operation] == old:
            next_operation += 1
        elif old in sites:
            removed.append(old)
        else:
            return False
    if next_operation != len(after):
        return False
    unresolved = set()
    operations = set(after)
    for old in removed:
        names, proofs = sites[old]
        if any((parent is not None and parent not in operations) or modified.intersection(dependencies)
               for parent, dependencies in proofs):
            return False
        unresolved.update(names)
    return not any(
        isinstance(node, ast.Name) and node.id in unresolved
        or isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in unresolved
        for node in ast.walk(_ast(output))
    )


_IMPORT_OPERATION = re.compile(r"\s*(?:import|from|raise)\b")
_COMMENT_OR_BLANK = re.compile(r"\s*(?:#.*)?\Z")
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
_IMPLICIT_NAMES = frozenset(vars(builtins)) | frozenset(keyword.kwlist) | {
    "__file__", "__cached__", "__package__", "__loader__", "__spec__", "__annotations__",
}


def _unbound_eager_name(change, original: str, output: str) -> bool:
    candidates = set(_IDENTIFIER.findall("\n".join(change.added_bodies))) - _IMPLICIT_NAMES
    new = {name for name in candidates if name not in original}
    if not new:
        return False
    tree = _ast(output)
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            if node.name == "*":
                return False
            new.discard(node.asname or node.name.split(".", 1)[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            new.discard(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)):
            new.discard(node.name)
        elif isinstance(node, ast.arg):
            new.discard(node.arg)
    return bool(new) and any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                             and node.id in new for node in _eager_nodes(tree))


def _literal_edit(change, original, output, ranges) -> bool:
    lines = change.added_bodies + change.removed_bodies
    if not ranges or not lines or not all(_COMMENT_OR_BLANK.fullmatch(line) for line in lines):
        return False
    anchors = change.old_lines | change.context_lines
    if anchors and not any(lo <= n <= hi for lo, hi in ranges for n in anchors):
        return False
    return not _same(_ast(original), _ast(output))


def python_risk(
    patch: str, changes, index: Index, validated: dict[str, str], import_changes: set[str],
    test_changes: dict[str, set[int]],
) -> str | None:
    """Full-suite reason when a Python edit cannot be reconstructed and compiled.

    ``compile`` does not execute the file. A rename, deletion, mismatched hunk,
    or syntax error can fail import or collection for every exposed test.
    """
    modified = {path for change in changes for path in (change.old_path, change.new_path) if path}
    for change in changes:
        old, new = change.old_path, change.new_path
        if not any(path and path.endswith(".py") for path in (old, new)):
            continue
        if old != new:
            return "python_path_changed"
        original = index.sources.get(old)
        if original is None and index.test_sources and old in index.test_sources:
            original = zlib.decompress(index.test_sources[old]).decode("utf-8")
        if original is None:
            return "python_source_missing"
        output = _patched_source(old, original, patch)
        if output is None:
            return "python_context"
        try:
            # The anchored source and precomputed comparison site establish
            # syntax when the sole edit exchanges equivalent operator tokens.
            same_operator = (
                len(changes) == 1
                and len(change.old_lines) == len(change.added_bodies) == len(change.removed_bodies) == 1
                and next(iter(change.old_lines)) in (index.operator_lines or {}).get(old, ())
                and _same_operator(old, change.removed_bodies[0], change.added_bodies[0])
            )
            if not same_operator:
                binary = output.encode("utf-8")
                if "coding" in "\n".join(output.split("\n", 2)[:2]):
                    encoding = tokenize.detect_encoding(BytesIO(binary).readline)[0]
                    if encoding != "utf-8":
                        return "python_encoding_changed"
                compile(binary, old, "exec", dont_inherit=True)
            if old.startswith("tests/"):
                body_changes = _test_body_changes(original, output)
                if body_changes is None:
                    return "python_test_collection"
                changed_methods, terminal_only = body_changes
                isolated = (index.isolated_test_methods or {}).get(old, {})
                if len(changes) == 1 and terminal_only and changed_methods and changed_methods <= isolated.keys():
                    test_changes[old] = {n for name in changed_methods for n in isolated[name]}
            if old.startswith("sqlglot/"):
                if old.endswith("/__init__.py") and not _same(_ast(original), _ast(output)):
                    return "python_package_init"
                eager_change = False
                if any(_IMPORT_OPERATION.match(line) for line in change.added_bodies + change.removed_bodies):
                    before, after = _import_time_operations(original), _import_time_operations(output)
                    sites = (index.redundant_import_sites or {}).get(old)
                    eager_change = before != after and not _only_unused_redundant_imports(
                        before, after, output, sites, modified,
                    )
                if eager_change or _unbound_eager_name(change, original, output) or _literal_edit(
                    change, original, output, (index.multiline_literals or {}).get(old, ()),
                ):
                    if not index.import_test_files or not index.import_test_files.get(old):
                        return "python_import_operation"
                    import_changes.add(old)
        except (SyntaxError, ValueError):
            return "python_syntax"
        validated[old] = output
    return None


def _ast(source: str) -> ast.Module:
    return ast.parse(source, type_comments=True)


def _boolean_flag_flip(pattern, old: str, new: str) -> bool:
    before = pattern.fullmatch(old)
    after = pattern.fullmatch(new)
    return bool(before and after and before[2] != after[2]
                and before[1] == after[1] and before[3] == after[3])


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
        if len(left) != len(right):
            return False
        for first, second in zip(left, right):
            if not _same(first, second):
                return False
        return True
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


_RUNTIME_FIELDS = {
    ast.Assign: "value", ast.AugAssign: "value", ast.Return: "value",
    ast.Expr: "value", ast.If: "test", ast.While: "test", ast.For: "iter",
}


def local_effect_lines(original: str, updated: str) -> list[set[int]] | None:
    """Find evaluation sites when only existing function expressions change."""
    sites: list[set[int]] = []

    def compare(old, new, in_function=False):
        if type(old) is not type(new):
            return False
        if isinstance(old, list):
            return len(old) == len(new) and all(compare(a, b, in_function) for a, b in zip(old, new))
        if not isinstance(old, ast.AST):
            return old == new
        runtime_field = _RUNTIME_FIELDS.get(type(old)) if in_function else None
        if isinstance(old, (ast.Assign, ast.AugAssign, ast.Expr)):
            # Keep declarations, descriptors, docstrings and global writes in the wider path.
            if isinstance(old, ast.Assign) and any(not isinstance(t, ast.Name) for t in old.targets):
                runtime_field = None
            if isinstance(old, ast.AugAssign) and not isinstance(old.target, ast.Name):
                runtime_field = None
            if isinstance(old, ast.Expr) and not isinstance(old.value, ast.Call):
                runtime_field = None
        for name in old._fields:
            left, right = getattr(old, name, None), getattr(new, name, None)
            if name == runtime_field and not _same(left, right):
                if not all(isinstance(v, ast.expr) and _safe_expression(v) for v in (left, right)):
                    return False
                if isinstance(old, ast.Expr) and not isinstance(right, ast.Call):
                    return False
                sites.append({old.lineno, left.lineno})
                continue
            nested_function = isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef))
            nested_scope = nested_function or isinstance(old, ast.ClassDef)
            inside = name == "body" and nested_function if nested_scope else in_function
            if not compare(left, right, inside):
                return False
        return True

    return sites if compare(_ast(original), _ast(updated)) and sites else None


def changed_function_bodies(original: str, updated: str) -> list[int] | None:
    """Recognize unchanged declarations whose existing bodies are the only edit."""
    trees = [_ast(original), _ast(updated)]

    def functions(tree):
        result = []

        def inspect(statements):
            for node in statements:
                if isinstance(node, ast.ClassDef):
                    inspect(node.body)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.append((node, node.body, ast.get_docstring(node, clean=False)))
                    node.body = []

        for directive in tree.type_ignores:
            directive.lineno = 0
        inspect(tree.body)
        return result

    def generator_body(body):
        pending = body[:]
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                return True
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                pending.extend(ast.iter_child_nodes(node))
        return False

    before, after = map(functions, trees)
    if not _same(*trees):
        return None
    changed = []
    for (node, body, doc), (_, replacement, new_doc) in zip(before, after, strict=True):
        if _same(body, replacement):
            continue
        if doc != new_doc or generator_body(body) != generator_body(replacement):
            return None
        if node.decorator_list and not all(
            isinstance(d, ast.Name) and d.id in {"staticmethod", "classmethod", "property"}
            for d in node.decorator_list
        ):
            return None
        changed.append(node.lineno)
    return changed or None


def table_comment_operations_only(original: str, updated: str) -> bool:
    """Prove that a table parser edit only moves comments on dot separators."""
    combine = ast.parse("if has_dot and self._prev_comments:\n    comments = comments + self._prev_comments").body[0]
    attach = ast.parse(
        "self._match(TokenType.DOT, expression=table if isinstance(table, exp.Expr) else None)",
        mode="eval",
    ).body
    bare_match = ast.parse("self._match(TokenType.DOT)", mode="eval").body

    class WithoutComments(ast.NodeTransformer):
        def __init__(self, method):
            self.method = method

        def visit_If(self, node):
            if self.method == "_parse_table_parts_fast" and _same(combine, node):
                return None
            return self.generic_visit(node)

        def visit_Call(self, node):
            if self.method == "_parse_table_parts" and _same(attach, node):
                return bare_match
            return self.generic_visit(node)

    trees = [_ast(original), _ast(updated)]
    if _same(*trees):
        return False
    for tree in trees:
        for directive in tree.type_ignores:
            directive.lineno = 0
        parser = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Parser"), None)
        if parser is None:
            return False
        for method in parser.body:
            if isinstance(method, ast.FunctionDef) and method.name in {"_parse_table_parts", "_parse_table_parts_fast"}:
                WithoutComments(method.name).visit(method)
    return _same(*trees)


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
    test_sources: dict | None = None
    import_test_files: dict | None = None
    multiline_literals: dict | None = None
    expression_flag_sites: dict | None = None
    show_flag_sites: dict | None = None
    isolated_test_methods: dict | None = None
    filtered_sql_fixtures: dict | None = None
    parser_comment_free: set[int] | None = None
    direct_fixture_readers: dict | None = None
    function_scopes: dict | None = None
    inherited_dialect_tests: dict | None = None
    redundant_import_sites: dict | None = None
    isolated_lines: dict | None = None
    fixture_reads: dict | None = None
    isolated_untraced: tuple = ()

    @classmethod
    def load(cls, path: Path, ids: dict[str, int], sha: str) -> Index | None:
        try:
            with gzip.open(path, "rb") as stream:
                data = marshal.loads(stream.read(), allow_code=False)
            if data["schema"] != 3 or data["pinned_sha"] != sha or data["test_ids"] != list(ids):
                return None
            result = cls(data["sources"], data["lines"], set(map(ids.__getitem__, data["preserve"])),
                       data.get("tokenizer"), data.get("primary"), data.get("operator_lines"),
                       data.get("test_sources"), data.get("import_test_files"),
                       data.get("multiline_literals"), data.get("expression_flag_sites"),
                       data.get("show_flag_sites"), data.get("isolated_test_methods"),
                       data.get("filtered_sql_fixtures"), set(data.get("parser_comment_free", ())),
                       data.get("direct_fixture_readers"), data.get("function_scopes"),
                       data.get("inherited_dialect_tests"), data.get("redundant_import_sites"))
            extra = path.with_name("isolated.gz")
            if extra.is_file():
                with gzip.open(extra, "rb") as stream:
                    pin, inventory, lines, fixtures, partial = marshal.loads(stream.read(), allow_code=False)
                if pin == sha and inventory == list(ids):
                    result.isolated_lines = lines
                    result.fixture_reads = fixtures
                    result.isolated_untraced = tuple(partial)
            return result
        except (OSError, EOFError, ValueError, KeyError, TypeError):
            return None

    def _hits(self, path: str, line: int) -> set[int]:
        posting = self.lines[path].get(str(line))
        hits = self.isolated_hits(path, (line,))
        if posting is not None:
            hits.update(n for n, in struct.iter_unpack("<H", base64.b64decode(posting)))
        return hits

    @staticmethod
    def numbers(mask):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask ^= bit

    def isolated_hits(self, path, lines):
        observations = (self.isolated_lines or {}).get(path, {})
        mask = 0
        for line in lines:
            mask |= observations.get(line, 0)
        return set(self.numbers(mask))

    def comment_free_tests(self, validated: dict[str, str]) -> set[int]:
        path = "sqlglot/parser.py"
        output = validated.get(path)
        if self.parser_comment_free and output is not None and table_comment_operations_only(self.sources[path], output):
            return self.parser_comment_free
        return set()

    def fixture_equivalent(self, patch: str, change) -> bool:
        path = change.old_path
        if path != change.new_path:
            return False
        source = (self.filtered_sql_fixtures or {}).get(path)
        bodies = change.added_bodies + change.removed_bodies
        if source is None or not all(not line or line.startswith("--") for line in bodies):
            return False
        original = zlib.decompress(source).decode("utf-8")
        updated = _patched_source(path, original, patch)
        if updated is None:
            return False
        def visible(text):
            return [line for line in text.splitlines() if line and not line.startswith("--")]

        return visible(original) == visible(updated)

    def narrow(self, patch, change, *, allow_equivalence: bool = True,
               validated: dict[str, str] | None = None) -> set[int] | None:
        path = change.old_path
        if path != change.new_path or path not in self.sources:
            return None
        source = self.sources[path]
        output = validated.get(path) if validated is not None else _patched_source(path, source, patch)
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
                for sites, pattern in (
                    (self.expression_flag_sites, _EXPRESSION_FLAG), (self.show_flag_sites, _SHOW_FLAG),
                ):
                    hits = (sites or {}).get(path, {}).get(line)
                    # Observed consumers remain relevant in a mixed patch.
                    # Only an empty inference needs the single-file guard.
                    if hits is not None and (hits or allow_equivalence) and _boolean_flag_flip(
                        pattern, change.removed_bodies[0], change.added_bodies[0],
                    ):
                        return set(hits)
                if self.tokenizer is not None:
                    keys = _escape_keys(path, source, output, line)
                    if keys is not None:
                        # Class initialization and escaping output inspect only
                        # key presence. Decoder value reads are indexed by key.
                        hits = set(self.tokenizer["unobserved"]) | {
                            n for key in keys for n in self.tokenizer["keys"].get(key, ())
                        }
                        if hits or allow_equivalence:
                            return hits
                entry = _predicate_entry(source, output, line)
            except (SyntaxError, ValueError, TypeError):
                return None
            if entry is not None:
                return self._hits(path, entry)
            if classify_line(path, source, line, change.added_bodies[0]) == "exact":
                return self._hits(path, line)
        if not change.structural and path in self.lines:
            sites = local_effect_lines(source, output)
            if sites:
                observations = [set().union(*(self._hits(path, line) for line in site)) for site in sites]
                # An unobserved evaluation site stays in the broader fallback.
                if all(observations):
                    return set().union(*observations)
        scopes = (self.function_scopes or {}).get(path)
        if scopes:
            bodies = changed_function_bodies(source, output)
            if bodies and all(line in scopes for line in bodies):
                hits = {test for line in bodies for test in scopes[line]}
                if self.isolated_lines:
                    for node in ast.walk(_ast(source)):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno in bodies:
                            hits.update(self.isolated_hits(path, range(node.body[0].lineno, node.end_lineno + 1)))
                return hits
        return None
