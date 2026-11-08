"""The Mac Studio service runs in its own venv built from requirements.txt (scripts/install_studio_on_mac.sh).
A module that imports anything else passes the tests here (.venv has more) and then the service fails to start —
which happened with numpy in local_studio/grade.py on 2026-10-28."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# import name of each requirement and of the packages they bring along
FROM_REQUIREMENTS = {"fastapi", "httpx", "multipart", "python_multipart", "uvicorn", "websockets", "PIL", "pydantic",
                     "pydantic_core", "starlette", "anyio", "typing_extensions"}


def imported(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_studio_imports_only_what_its_venv_installs():
    # Motion Director checks reuse two stdlib-only repo scripts (the service runs from the repo root).
    files = [*(ROOT / "local_studio").rglob("*.py"), ROOT / "app" / "director_config.py", ROOT / "app" / "shot_lint.py",
             ROOT / "scripts" / "h3_graph_validate.py", ROOT / "scripts" / "h3_motion_director_probe.py"]
    allowed = set(sys.stdlib_module_names) | FROM_REQUIREMENTS | {"local_studio", "app", "scripts"}
    foreign = {f"{path.relative_to(ROOT)}: {name}" for path in files for name in imported(path) - allowed}
    assert not foreign, "Studio venv (requirements.txt) has no: " + ", ".join(sorted(foreign))
