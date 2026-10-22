import importlib
import pkgutil

import vesper


def test_every_vesper_module_imports():
    failures = []
    for mod in pkgutil.walk_packages(vesper.__path__, prefix="vesper."):
        try:
            importlib.import_module(mod.name)
        except Exception as e:  # report all, not just the first
            failures.append(f"{mod.name}: {type(e).__name__}: {e}")
    assert failures == []


def test_no_import_of_missing_vesper_module_anywhere():
    """Also catches imports inside functions, which test_every_vesper_module_imports cannot see."""
    import ast
    import importlib.util
    from pathlib import Path

    missing = []
    for path in Path(vesper.__path__[0]).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for n in names:
                if n.split(".")[0] == "vesper" and importlib.util.find_spec(n) is None:
                    missing.append(f"{path.name}:{node.lineno} -> {n}")
    assert missing == []
