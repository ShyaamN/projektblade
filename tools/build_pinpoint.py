"""Package pinned source and complete-call coverage for the selector."""

import ast
import base64
import gzip
import json
import marshal
from pathlib import Path
import subprocess
import struct
import zlib
from collections import defaultdict


ROOT = Path(__file__).resolve().parents[1]
PINNED = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def import_exposure(maps, coverage):
    """Union cold imports, warm file observations and the base import graph."""
    with (maps / "cold-imports.json").open(encoding="utf-8") as stream:
        cold = json.load(stream)
    if cold["pinned_sha"] != PINNED or cold["test_ids"] != coverage["test_ids"]:
        raise RuntimeError("Cold import inventory mismatch")
    impact = json.loads((maps / "impact.json").read_text(encoding="utf-8"))
    module_to_path = impact["modules"]
    module_imports = impact["module_imports"]
    by_module = defaultdict(set)
    unobserved = set()
    for test_file, loaded in cold["imports"].items():
        if loaded is None:
            unobserved.add(test_file)
            continue
        for path in loaded:
            by_module[path].add(test_file)
        pending = list(impact["test_file_imports"].get(test_file, ()))
        seen = set()
        while pending:
            module = pending.pop()
            if module in seen:
                continue
            seen.add(module)
            if module in module_to_path:
                by_module[module_to_path[module]].add(test_file)
            pending.extend(module_imports.get(module, ()))
            if "." in module:
                pending.append(module.rsplit(".", 1)[0])
    test_files = [node.split("::", 1)[0] for node in coverage["test_ids"]]
    for path, bucket in coverage["files"].items():
        for ids in bucket.values():
            by_module[path].update(test_files[n] for n in ids)
    return {path: sorted(files | unobserved) for path, files in by_module.items()}


def expression_flag_sites(base, maps, trees, coverage):
    """Index pinned expression subclasses whose schema consumers resolve by name."""
    classes = {}
    for path, tree in trees.items():
        if not path.startswith("sqlglot/expressions/"):
            continue
        for node in tree.body:
            if (isinstance(node, ast.ClassDef) and len(node.bases) == 1
                    and isinstance(node.bases[0], ast.Name) and node.bases[0].id == "Expression"
                    and not node.decorator_list and not node.keywords):
                for stmt in node.body:
                    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "arg_types"
                            and isinstance(stmt.value, ast.Dict) and all(
                                isinstance(k, ast.Constant) and isinstance(k.value, str)
                                and isinstance(v, ast.Constant) and type(v.value) is bool
                                for k, v in zip(stmt.value.keys, stmt.value.values)
                            )):
                        classes[node.name] = (path, [value.lineno for value in stmt.value.values])

    hits = defaultdict(set)
    unsupported = set()
    tests_by_file = defaultdict(list)
    for n, node_id in enumerate(coverage["test_ids"]):
        tests_by_file[node_id.split("::", 1)[0]].append((node_id, n))

    def inspect(path, node, function=None, parents=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                inspect(path, stmt, node.name, (*parents, node))
            # Signature references can appear in decorators and default values.
            for expr in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
                if expr is not None:
                    inspect(path, expr, function, (*parents, node))
            return
        name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
        if name in classes:
            if path in tests_by_file:
                items = tests_by_file[path]
                matching = [n for node_id, n in items if function in node_id.split("::")[1:]]
                hits[name].update(matching or (n for _, n in items))
            elif function:
                hits[name].update(coverage["files"].get(path, {}).get(str(node.lineno), ()))
            else:
                registration = False
                if (len(parents) >= 2 and isinstance(parents[-1], ast.Dict)
                        and any(node is key for key in parents[-1].keys)):
                    stmt = parents[-2]
                    targets = (stmt.targets if isinstance(stmt, ast.Assign)
                               else [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
                    registration = any(isinstance(t, ast.Name) and t.id == "PROPERTIES_LOCATION"
                                       for t in targets)
                if not registration:
                    unsupported.add(name)
        for child in ast.iter_child_nodes(node):
            inspect(path, child, function, (*parents, node))

    # Scan the entire pinned package, including files without dynamic coverage.
    for source in (base / "sqlglot").rglob("*.py"):
        path = source.relative_to(base).as_posix()
        inspect(path, trees[path] if path in trees else ast.parse(source.read_text(encoding="utf-8")))
    for path in tests_by_file:
        source = base / path
        if source.is_file():
            inspect(path, ast.parse(source.read_text(encoding="utf-8")))

    supplemental = json.loads((maps / "expression-instances.json").read_text(encoding="utf-8"))
    ids = {node: i for i, node in enumerate(coverage["test_ids"])}
    expected = {node for node in ids if "test_max_nodes" in node}
    if supplemental["pinned_sha"] != PINNED or set(supplemental["constructed"]) != expected:
        raise RuntimeError("Expression instance inventory mismatch")
    for node, names in supplemental["constructed"].items():
        for name in names:
            hits[name].add(ids[node])

    sites = defaultdict(dict)
    for name, (path, lines) in classes.items():
        if name not in unsupported and hits[name]:
            sites[path].update((line, sorted(hits[name])) for line in lines)
    return dict(sites)


def show_flag_sites(maps, coverage, trees):
    data = json.loads((maps / "show-dispatch.json").read_text(encoding="utf-8"))
    if data["pinned_sha"] != PINNED or data["test_ids"] != coverage["test_ids"]:
        raise RuntimeError("SHOW dispatch inventory mismatch")
    ids = {node: i for i, node in enumerate(coverage["test_ids"])}
    unobserved = {ids[node] for node in data["unobserved"]}
    sites = defaultdict(dict)
    for path, observations in data["keys"].items():
        if path not in trees:
            continue
        for cls in trees[path].body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for stmt in cls.body:
                if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict)
                        and any(isinstance(t, ast.Name) and t.id == "SHOW_PARSERS" for t in stmt.targets)):
                    continue
                for key, value in zip(stmt.value.keys, stmt.value.values):
                    if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                            and isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                            and value.func.id == "_show_parser" and any(
                                kw.arg == "target" and isinstance(kw.value, ast.Constant)
                                and type(kw.value.value) is bool for kw in value.keywords
                            )):
                        hits = {ids[node] for node in observations.get(key.value, ())}
                        sites[path][key.lineno] = sorted(unobserved | hits)
    return dict(sites)


def isolated_test_methods(base, nodes):
    """Index test bodies with no direct callers or known inheriting classes."""
    referenced = set()
    inherited = set()
    for source in (base / "tests").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.alias):
                referenced.add(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith("test_"):
                    referenced.add(node.value)
            elif isinstance(node, ast.ClassDef):
                for parent in node.bases:
                    for name in ast.walk(parent):
                        if isinstance(name, ast.Name):
                            inherited.add(name.id)
                        elif isinstance(name, ast.Attribute):
                            inherited.add(name.attr)
    methods = defaultdict(dict)
    files_by_name = defaultdict(set)
    for i, node in enumerate(nodes):
        path, *parts = node.split("::")
        if not 1 <= len(parts) <= 2:
            continue
        parts[-1] = parts[-1].split("[", 1)[0]
        name = "::".join(parts)
        files_by_name[name].add(path)
        if parts[-1] not in referenced and (len(parts) == 1 or parts[0] not in inherited):
            methods[path].setdefault(name, []).append(i)
    return {path: {name: ids for name, ids in names.items() if len(files_by_name[name]) == 1}
            for path, names in methods.items()}


def merge_stress_lines(maps, coverage):
    stress = json.loads((maps / "stress-lines.json").read_text(encoding="utf-8"))
    ids = {node: i for i, node in enumerate(coverage["test_ids"])}
    expected = {node for node in ids if "test_max_nodes" in node}
    if stress["pinned_sha"] != PINNED or set(stress["nodes"]) != expected:
        raise RuntimeError("Stress trace inventory mismatch")
    for node, files in stress["nodes"].items():
        for path, lines in files.items():
            postings = coverage["files"].setdefault(path, {})
            for line in lines:
                key = str(line)
                postings[key] = sorted({*postings.get(key, ()), ids[node]})


def direct_json_fixtures(base, coverage):
    """Verify literal FIXTURES_DIR reads before packaging their exact postings."""
    candidates = {path: hits[0] for path, hits in coverage["fixtures"].items()
                  if path.endswith(".json") and len(hits) == 1}
    basenames = {Path(path).name for path in candidates}
    references = defaultdict(list)
    for root in (base / "tests", base / "sqlglot"):
        for source in root.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            if not any(name in text for name in basenames):
                continue
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in basenames:
                    references[node.value].append((source.relative_to(base).as_posix(), tree, node))

    verified = {}
    for path, number in candidates.items():
        parts = path.split("/")
        postings = references[parts[-1]]
        filename, *scope = coverage["test_ids"][number].split("::")
        if len(postings) != 1 or len(scope) != 2 or postings[0][0] != filename:
            continue
        _, tree, literal = postings[0]
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name != scope[0]:
                continue
            for method in cls.body:
                if not isinstance(method, ast.FunctionDef) or method.name != scope[1] or method.decorator_list:
                    continue
                target = ast.parse("open(os.path.join(FIXTURES_DIR, " + ", ".join(map(repr, parts)) + "))",
                                   mode="eval").body
                for call in ast.walk(method):
                    if (isinstance(call, ast.Call) and literal in ast.walk(call) and call.args
                            and ast.dump(call.func) == ast.dump(target.func)
                            and ast.dump(call.args[0]) == ast.dump(target.args[0])):
                        verified[f"tests/fixtures/{path}"] = [number]
    return verified


def cold_line_postings(maps, coverage):
    with gzip.open(maps / "cold-lines.json.gz", "rt", encoding="utf-8") as stream:
        cold = json.load(stream)
    if cold["pinned_sha"] != PINNED or cold["test_ids"] != coverage["test_ids"]:
        raise RuntimeError("Cold line inventory mismatch")
    by_file = defaultdict(set)
    for i, node in enumerate(coverage["test_ids"]):
        by_file[node.split("::", 1)[0]].add(i)
    if by_file.keys() != cold["lines"].keys() or any(files is None for files in cold["lines"].values()):
        raise RuntimeError("Cold line collection is incomplete")
    eager_lines = defaultdict(lambda: defaultdict(set))
    for test_file, paths in cold["lines"].items():
        for path, hits in paths.items():
            for line in hits:
                eager_lines[path][line].update(by_file[test_file])
    return eager_lines


def function_scope_postings(trees, coverage, eager_lines):
    result = {}
    for path, tree in trees.items():
        warm = coverage["files"].get(path, {})
        cold = eager_lines.get(path, {})
        postings = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            hits = set()
            for line in range(node.body[0].lineno, node.end_lineno + 1):
                hits.update(warm.get(str(line), ()))
                hits.update(cold.get(line, ()))
            if hits:
                postings[node.lineno] = sorted(hits)
        if postings:
            result[path] = postings
    return result


def inherited_dialect_tests(trees, coverage):
    roots = {"dialects", "generators", "parsers"}
    modules = {path[:-3].replace("/", "."): path for path in trees if path.split("/")[1] in roots}
    classes = {f"{module}.{cls.name}": path for module, path in modules.items()
               for cls in trees[path].body if isinstance(cls, ast.ClassDef)}
    descendants = defaultdict(set)
    for module, path in modules.items():
        aliases = {}
        for stmt in trees[path].body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
                aliases.update((item.asname or item.name, f"{stmt.module}.{item.name}") for item in stmt.names)
            elif isinstance(stmt, ast.Import):
                aliases.update((item.asname or item.name.split(".")[0],
                                item.name if item.asname else item.name.split(".")[0]) for item in stmt.names)
        for cls in trees[path].body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for base in cls.bases:
                name = ast.unparse(base)
                head, *suffix = name.split(".")
                target = ".".join((aliases.get(head, head if suffix else f"{module}.{head}"), *suffix))
                parent = classes.get(target)
                if parent and parent != path:
                    descendants[parent].add(path)

    tests = defaultdict(list)
    for i, node in enumerate(coverage["test_ids"]):
        tests[node.split("::", 1)[0]].append(i)
    result = {}
    for path, children in descendants.items():
        pending, visited = list(children), {path}
        while pending:
            child = pending.pop()
            if child in visited:
                continue
            visited.add(child)
            pending.extend(descendants.get(child, ()))
        own = Path(path).stem
        names = {Path(child).stem for child in visited} - {own}
        mandatory = {i for name in names for i in tests[f"tests/dialects/test_{name}.py"]}
        if mandatory:
            result[path] = sorted(mandatory | set(tests[f"tests/dialects/test_{own}.py"]))
    return result


def redundant_import_sites(trees):
    """Record top-level imports whose provider is already loaded."""
    modules = {path[:-3].replace("/", ".").removesuffix(".__init__"): path for path in trees}

    def providers(stmt):
        if isinstance(stmt, ast.Import):
            return {alias.name for alias in stmt.names}
        if isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module != "__future__":
            return {stmt.module, *(f"{stmt.module}.{alias.name}" for alias in stmt.names
                                   if f"{stmt.module}.{alias.name}" in modules)}
        return set()

    def direct_imports(module):
        if module not in modules:
            return set()
        return {target for stmt in trees[modules[module]].body for target in providers(stmt)}

    direct = {module: direct_imports(module) for module in modules}

    def paths_from(starts, current):
        paths = {}
        queue = [(module, ()) for module in sorted(starts)]
        for module, parents in queue:
            if module in paths:
                continue
            paths[module] = (*parents, module)
            queue.extend((child, paths[module]) for child in sorted(direct.get(module, ())))
        return paths if current not in paths else {}

    referenced = defaultdict(set)
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in modules:
                referenced[node.module].update(a.name for a in node.names)
    result = {}
    for module, path in modules.items():
        if not path.startswith(("sqlglot/parsers/", "sqlglot/generators/", "sqlglot/dialects/")):
            continue
        prior = {target: (None, sorted(modules[name] for name in chain if name in modules))
                 for target, chain in paths_from({"sqlglot"}, module).items()}
        safe = {}
        for stmt in trees[path].body:
            provided = providers(stmt)
            if not provided:
                continue
            literal = ast.dump(stmt)
            names = {a.asname or a.name.split(".")[0] for a in stmt.names}
            if provided <= prior.keys() and "*" not in names and not names & referenced[module] and "*" not in referenced[module]:
                safe[literal] = (sorted(names), [prior[item] for item in sorted(provided)])
            for target, chain in paths_from(provided, module).items():
                proof = (literal, sorted(modules[name] for name in chain if name in modules))
                prior.setdefault(target, proof)
        if safe:
            result[path] = safe
    return result


def main():
    base = ROOT / "vendor/sqlglot"
    head = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
    if head != PINNED or subprocess.check_output(
        ["git", "-C", str(base), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip():
        raise RuntimeError("Coverage map requires an unchanged pinned checkout")
    maps = ROOT / "maps"
    with gzip.open(maps / "coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    summary = json.loads((maps / "coverage_summary.json").read_text(encoding="utf-8"))
    if (
        coverage["pinned_sha"] != PINNED
        or summary["matched_tests"] != coverage["test_count"]
        or summary["partial"]
        or summary["pytest_exit"]
        or summary["unattributed_submits"]
        or coverage["child_jobs_submitted"] != summary["child_jobs_submitted"]
        or coverage["child_jobs"] != summary["child_jobs"]
    ):
        raise RuntimeError("Child coverage is incomplete or misattributed")
    merge_stress_lines(maps, coverage)
    # Trace availability and source availability are independent. A valid edit
    # to a pinned but untraced module still needs syntax validation and routing.
    sources = {
        path.relative_to(base).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((base / "sqlglot").rglob("*.py"))
    }
    preserve = set(summary["nodes_short"])
    lines = {}
    operator_lines = {}
    multiline_literals = {}
    trees = {path: ast.parse(source) for path, source in sources.items()}
    eager_lines = cold_line_postings(maps, coverage)
    for path, tree in trees.items():
        keep = {
            str(node.test.lineno if isinstance(node, ast.If) else node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, (ast.If, ast.Assign))
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.Assign, ast.AugAssign, ast.Return, ast.Expr)):
                entry = getattr(node, "test", None) or getattr(node, "iter", None) or getattr(node, "value", None)
                if entry is not None:
                    keep.update((str(node.lineno), str(entry.lineno)))
        warm = coverage["files"].get(path, {})
        cold = eager_lines.get(path, {})
        postings = {}
        for line in sorted(keep, key=int):
            hits = sorted(set(warm.get(line, ())) | set(cold.get(int(line), ())))
            if hits:
                postings[line] = base64.b64encode(struct.pack(f"<{len(hits)}H", *hits)).decode()
        lines[path] = postings
        checked = sorted({
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
            and node.left.attr in {"normalization_strategy", "version"}
            and len(node.ops) == 1
        })
        if checked:
            operator_lines[path] = checked
        spans = sorted({
            (node.lineno, node.end_lineno) for node in ast.walk(tree)
            if (isinstance(node, ast.JoinedStr)
                or isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))
            and node.end_lineno > node.lineno
        })
        if spans:
            multiline_literals[path] = spans
    with gzip.open(maps / "coverage.json.gz", "rt", encoding="utf-8") as stream:
        primary = json.load(stream)
    if primary["pinned_sha"] != PINNED or primary["test_ids"] != coverage["test_ids"]:
        raise RuntimeError("Parent coverage inventory mismatch")
    merge_stress_lines(maps, primary)
    primary["files"] = {
        path: zlib.compress(
            marshal.dumps({int(line): hits for line, hits in bucket.items()}, 4, allow_code=False),
            level=1,
        )
        for path, bucket in primary["files"].items()
    }
    primary["packed_files"] = True
    packaged = {
        "schema": 3,
        "pinned_sha": PINNED,
        "test_ids": coverage["test_ids"],
        "sources": sources,
        "lines": lines,
        "preserve": sorted(preserve),
        "primary": primary,
        "operator_lines": operator_lines,
        "import_test_files": import_exposure(maps, coverage),
        "multiline_literals": multiline_literals,
        "expression_flag_sites": expression_flag_sites(base, maps, trees, coverage),
        "show_flag_sites": show_flag_sites(maps, coverage, trees),
        "isolated_test_methods": isolated_test_methods(base, coverage["test_ids"]),
        "direct_fixture_readers": direct_json_fixtures(base, coverage),
        "function_scopes": function_scope_postings(trees, coverage, eager_lines),
        "inherited_dialect_tests": inherited_dialect_tests(trees, coverage),
        "redundant_import_sites": redundant_import_sites(trees),
        # The pinned SQL fixture readers in tests/helpers.py both remove
        # column-zero '--' lines and empty lines before parsing the file.
        "filtered_sql_fixtures": {
            path.relative_to(base).as_posix(): zlib.compress(path.read_bytes())
            for path in sorted((base / "tests/fixtures").rglob("*.sql"))
        },
        "test_sources": {
            path: zlib.compress((base / path).read_text(encoding="utf-8").encode())
            for path in sorted({node.split("::", 1)[0] for node in coverage["test_ids"]})
            if path.startswith("tests/") and path.endswith(".py") and (base / path).is_file()
        },
    }
    observations = maps / "tokenizer-observations.json"
    if observations.exists():
        tokenizer = json.loads(observations.read_text(encoding="utf-8"))
        if tokenizer["pinned_sha"] != PINNED or tokenizer["test_ids"] != coverage["test_ids"]:
            raise RuntimeError("Tokenizer observation inventory mismatch")
        number = {test: n for n, test in enumerate(coverage["test_ids"])}
        packaged["tokenizer"] = {
            "unobserved": sorted(number[test] for test in tokenizer["unobserved"]),
            "keys": {key: sorted(number[test] for test in tests) for key, tests in tokenizer["keys"].items()},
        }
    parser_observations = maps / "parser-tokens.json"
    if parser_observations.exists():
        profile = json.loads(parser_observations.read_text(encoding="utf-8"))
        if profile["pinned_sha"] != PINNED or profile["test_ids"] != coverage["test_ids"]:
            raise RuntimeError("Parser input inventory mismatch")
        number = {test: n for n, test in enumerate(coverage["test_ids"])}
        packaged["parser_comment_free"] = sorted(number[node] for node in profile["comment_free"])
    data = marshal.dumps(packaged, 4, allow_code=False)
    (maps / "pinpoint.marshal.gz").write_bytes(gzip.compress(data, mtime=0))


if __name__ == "__main__":
    main()
