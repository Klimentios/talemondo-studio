"""Code mode for the h3-studio MCP server: one short Python script calls many Studio tools in one round trip.

The script runs in a child python (-I -S -B) that has no network and no files to write: macOS sandbox-exec
denies network/file writes/fork at the kernel level, an audit hook refuses sockets, subprocesses, ctypes and
writes inside Python, and the script sees only an allowlist of pure modules. Every Studio tool is a function
in the script, but its call travels back over the pipe to the MCP server, which runs the same handler (and
therefore the same gates) an ordinary tool call would. Only what the script prints and returns comes back.
Standard library only: the MCP server runs on the system python3.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

PY_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "array": "list", "object": "dict"}
SAFE_MODULES = ("json", "re", "math", "statistics", "collections", "itertools", "functools", "operator", "datetime",
                "time", "textwrap", "difflib", "string", "dataclasses", "typing", "pprint", "copy", "decimal",
                "fractions", "random", "heapq", "bisect", "enum", "unicodedata", "base64", "hashlib")
# Kernel layer (macOS): the child cannot open any socket (loopback included), write a file or fork.
SANDBOX_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (literal "/dev/null"))
(deny process-fork)
"""


def py_type(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "Any"
    if schema.get("enum"):
        return "Literal[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"][:12]) + "]"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(PY_TYPES.get(k, "None" if k == "null" else "Any") for k in kind)
    if kind == "array" and isinstance(schema.get("items"), dict) and schema["items"].get("type") in PY_TYPES:
        return f"list[{py_type(schema['items'])}]"
    for union in ("anyOf", "oneOf"):
        if schema.get(union):
            return " | ".join(dict.fromkeys(py_type(s) for s in schema[union]))
    return PY_TYPES.get(kind, "Any")


def signature(name: str, description: str, schema: dict) -> str:
    """One typed line per tool, made from the very inputSchema the MCP client sees (no second list to drift)."""
    props, required = (schema or {}).get("properties") or {}, set((schema or {}).get("required") or ())
    args = [f"{k}: {py_type(v)}" for k, v in props.items() if k in required]
    args += [f"{k}: {py_type(v)} = None" for k, v in props.items() if k not in required]
    summary = (description or "").split(". ")[0].strip().rstrip(".")
    if len(summary) > 160:
        summary = summary[:157] + "…"
    return f"{name}({', '.join(args)})  # {summary}"


CHILD = r'''
import ast, io, json, os, sys, traceback, resource
cpu = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
proto_in, proto_out = sys.stdin, sys.__stdout__
job = json.loads(proto_in.readline())
SAFE = set(job["safe"])
for _name in SAFE:  # before the hook: their own imports and .pyc loads need the machinery
    __import__(_name)
BLOCKED_EVENTS = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                  "os.forkpty", "os.kill", "os.killpg", "ctypes.", "os.remove", "os.rmdir", "os.rename", "os.unlink",
                  "os.mkdir", "os.chmod", "os.chown", "os.symlink", "os.link", "os.truncate", "shutil.", "urllib.",
                  "http.", "webbrowser.", "pty.")
BLOCKED_MODULES = {"socket", "_socket", "ssl", "_ssl", "select", "selectors", "subprocess", "_posixsubprocess",
                   "ctypes", "_ctypes", "multiprocessing", "_multiprocessing", "asyncio", "urllib", "http",
                   "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "socketserver", "webbrowser",
                   "pty", "importlib", "_imp", "marshal", "os", "posix", "sys", "shutil", "pathlib", "io", "_io",
                   "builtins", "gc", "signal", "resource", "fcntl", "mmap"}

def hook(event, args):
    if event.startswith(BLOCKED_EVENTS):
        raise PermissionError(f"code mode: {event} is not allowed (no network, processes or file changes)")
    if event == "import" and args[0].split(".")[0] in BLOCKED_MODULES and args[0] not in sys.modules:
        raise PermissionError(f"code mode: import {args[0]} is not allowed")
    if event == "open" and isinstance(args[1], str) and any(c in args[1] for c in "wax+"):
        raise PermissionError("code mode: writing files is not allowed")
    if event == "open" and args[2] and (args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)):
        raise PermissionError("code mode: writing files is not allowed")

class StudioError(Exception):
    """A Studio tool refused or failed: the same message an ordinary tool call returns."""

calls = 0
def call(name, args=None, **kwargs):
    global calls
    if args is not None and not isinstance(args, dict):
        raise TypeError(f"{name}: pass keyword arguments or one dict")
    calls += 1
    proto_out.write(json.dumps({"call": name, "args": {**(args or {}), **kwargs}}, ensure_ascii=False, default=str) + "\n")
    proto_out.flush()
    reply = json.loads(proto_in.readline())
    if "error" in reply:
        raise StudioError(reply["error"])
    return reply["ok"]

def _function(name):
    def tool(args=None, **kwargs):
        return call(name, args, **kwargs)
    tool.__name__ = tool.__qualname__ = name
    return tool

real_import = __import__
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in SAFE:
        raise ImportError(f"code mode: import {name} is not allowed; allowed: {', '.join(sorted(SAFE))}")
    return real_import(name, globals, locals, fromlist, level)

import builtins
names = {k: getattr(builtins, k) for k in dir(builtins) if k not in
         {"open", "exec", "eval", "compile", "input", "breakpoint", "help", "exit", "quit", "__import__",
          "globals", "locals", "vars", "memoryview", "__loader__", "__spec__", "__build_class__"}}
names["__import__"] = safe_import
names["__build_class__"] = builtins.__build_class__
scope = {"__builtins__": names, "__name__": "studio_script", "StudioError": StudioError, "call": call,
         **{n: _function(n) for n in job["functions"]}}

# The script body becomes a function: top-level `return` works and a bare last expression is the result.
tree = ast.parse(job["code"], "<studio_script>")
if tree.body and isinstance(tree.body[-1], ast.Expr):
    tree.body[-1] = ast.Return(tree.body[-1].value)
module = ast.parse("def __studio_main__():\n    pass\n")
module.body[0].body = tree.body or [ast.Pass()]
module = ast.fix_missing_locations(module)
code = compile(module, "<studio_script>", "exec")
out = io.StringIO()
sys.stdout = out
sys.addaudithook(hook)
del resource
final = {"done": True, "calls": calls}
try:
    exec(code, scope)
    value = scope["__studio_main__"]()
    final["result"] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
except BaseException as exc:
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    final["error"] = f"{type(exc).__name__}: {exc}"
    final["traceback"] = "".join(line for line in tb if "<studio_script>" in line or line is tb[-1])[-2000:]
final["calls"] = calls
final["stdout"] = out.getvalue()
proto_out.write(json.dumps(final, ensure_ascii=False, default=str) + "\n")
proto_out.flush()
'''


def sandbox_command() -> tuple[list[str], str]:
    python = [sys.executable, "-I", "-S", "-B", "-c", CHILD]
    tool = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if tool and os.environ.get("H3_CODE_MODE_SANDBOX", "1") != "0":
        return [tool, "-p", SANDBOX_PROFILE, *python], "sandbox-exec + audit hook"
    return python, "audit hook"


def run(code: str, functions: list[str], dispatch, timeout: float = 60, max_calls: int = 200) -> dict:
    """Run `code` in the child; `dispatch(name, args)` executes each tool call here, in the server.

    `timeout` is the wall clock of the whole script including its Studio calls; a call already running in
    the server is not interrupted (its own HTTP timeout applies), the script is killed right after it."""
    command, layer = sandbox_command()
    started, log = time.monotonic(), []
    with tempfile.TemporaryDirectory(prefix="h3-code-") as cwd:
        proc = subprocess.Popen([*command, str(int(timeout) + 1)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, start_new_session=True,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
                                text=True, encoding="utf-8")
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        final, failure = None, None
        try:
            proc.stdin.write(json.dumps({"code": code, "functions": functions, "safe": SAFE_MODULES}) + "\n")
            proc.stdin.flush()
            while final is None:
                left = timeout - (time.monotonic() - started)
                if left <= 0 or not selector.select(left):
                    failure = f"timeout: the script ran longer than {timeout:g} s and was stopped"
                    break
                line = proc.stdout.readline()
                if not line:
                    failure = "the script process ended without a result"
                    break
                message = json.loads(line)
                if message.get("done"):
                    final = message
                    break
                name, args = message.get("call"), message.get("args") or {}
                if len(log) >= max_calls:
                    reply = {"error": f"code mode: more than {max_calls} tool calls in one script"}
                else:
                    t0 = time.monotonic()
                    try:
                        reply = {"ok": dispatch(name, args)}
                    except Exception as exc:  # the tool's own refusal goes back to the script as StudioError
                        reply = {"error": str(exc) or type(exc).__name__}
                    log.append({"tool": name, "ms": round((time.monotonic() - t0) * 1000), "ok": "ok" in reply})
                proc.stdin.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            failure = f"the script process broke the protocol: {exc}"
        finally:
            selector.close()
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            try:
                _, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stderr = ""
    by_tool: dict = {}
    for item in log:
        by_tool[item["tool"]] = by_tool.get(item["tool"], 0) + 1
    result = {"sandbox": layer, "seconds": round(time.monotonic() - started, 2), "tool_calls": len(log),
              "by_tool": by_tool, "studio_ms": sum(item["ms"] for item in log),
              **({"failed_calls": [item["tool"] for item in log if not item["ok"]]} if not all(i["ok"] for i in log) else {})}
    if final is None:
        result["error"] = failure + (f"\n{stderr.strip()[-1500:]}" if stderr and stderr.strip() else "")
        return result
    for key in ("result", "stdout", "error", "traceback"):
        if final.get(key) not in (None, ""):
            result[key] = final[key]
    return result
