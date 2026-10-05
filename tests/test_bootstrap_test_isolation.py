"""Collecting backend tests must never repair production resource inputs."""
import ast
import sys
from pathlib import Path


def test_backend_module_initialization_does_not_create_bootstrap(tmp_path):
    source = Path(__file__).with_name("test_backend.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    initializers = []
    for statement in module.body:
        if isinstance(statement, ast.ClassDef):
            break
        initializers.append(statement)
    checkout = tmp_path / "studio"
    tests = checkout / "tests"
    tests.mkdir(parents=True)
    namespace = {"__file__": str(tests / "test_backend.py"), "__name__": "isolated_backend_tests"}
    original_path = sys.path[:]
    try:
        exec(compile(ast.Module(body=initializers, type_ignores=[]), namespace["__file__"], "exec"), namespace)
    finally:
        sys.path[:] = original_path
    assert not (checkout / "bootstrap.sh").exists()
    assert list(checkout.iterdir()) == [tests]
