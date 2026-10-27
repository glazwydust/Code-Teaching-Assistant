"""run_code: check the code reads correctly, then run it safely on every test.

Steps (matches checks 1-3 in the project plan):
  1. Syntax check with Python's own parser (ast).
  2. Undefined-name check (pyflakes if installed, otherwise a simple built-in fallback).
  3. Run every test case in a SEPARATE process with a time limit.

Return format (agree this with P2/P3/P4):
{
  "status": "passed" | "failed" | "syntax_error",
  "syntax_error": None | {"line": int, "message": str},
  "warnings": [{"line": int, "message": str}],        # e.g. undefined names
  "num_passed": int, "num_total": int,
  "tests": [{"input": [...], "expected": ..., "actual": ..., "passed": bool,
             "error": None | str, "lines": [int]}]     # "lines" only when trace=True
}
"""
import ast
import builtins
import json
import os
import subprocess
import sys
import time

from problems import get_problem

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "_worker.py")

PER_TEST_TIMEOUT = 2.0   # seconds per test, INCLUDING starting a Python process (slow on Windows / when busy)
TIMEOUT_BUDGET = 4.0     # once tests that hit the limit have used this much time, the rest are skipped.
                         # Only hung tests count against it, so a slow start-up is never a "timeout".
                         # A program that hangs on every test therefore costs about 4-6 seconds in total.


def check_syntax(code):
    """Return None if the code parses, else {'line', 'message'}."""
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return {"line": exc.lineno or 1, "message": exc.msg}
    return None


def find_undefined_names(code):
    """Names that are used but never defined, as [{'line', 'message'}]."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    try:  # best option: pyflakes
        from pyflakes import checker
        result = []
        for msg in checker.Checker(tree).messages:
            if type(msg).__name__ == "UndefinedName":
                result.append({"line": msg.lineno, "message": msg.message % msg.message_args})
        return result
    except ImportError:
        pass
    # Fallback: scope-blind check (good enough for beginner programs).
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    return [
        {"line": n.lineno, "message": f"undefined name '{n.id}'"}
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined
    ]


def _run_one(code, func, args, expected, trace, timeout, prelude="", strict=True,
             check_mutation=False):
    # repr + ast.literal_eval (in the worker) keeps tuples as tuples; JSON would turn them into lists.
    payload = json.dumps({"code": code, "func": func, "args_repr": repr(args),
                          "expected_repr": repr(expected), "trace": trace,
                          "prelude": prelude, "strict_types": strict,
                          "check_mutation": check_mutation})
    try:
        proc = subprocess.run(
            [sys.executable, "-I", WORKER],  # -I: isolated mode, ignores env and cwd imports
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "actual": None, "lines": [],
                "error": "Timed out (possible infinite loop)"}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        err = (proc.stderr.strip().splitlines() or ["Program crashed"])[-1]
        return {"passed": False, "actual": None, "lines": [], "error": err}


def run_code(code, problem_id, trace=False):
    # problem_id is normally an id like "sum_list"; a problem dict (e.g. from nus.py) also works.
    problem = problem_id if isinstance(problem_id, dict) else get_problem(problem_id)
    result ={"status": "failed", "syntax_error": None, "warnings": [],
              "num_passed": 0, "num_total": len(problem["tests"]), "tests": []}

    syntax = check_syntax(code)
    if syntax:
        result["status"] = "syntax_error"
        result["syntax_error"] = syntax
        return result
    result["warnings"] = find_undefined_names(code)

    timeout_spent = 0.0   # seconds used by tests that hit the time limit
    for test in problem["tests"]:
        # a test is (args, expected), or (args, expected, function_name) when a problem
        # tests several functions (NUS question 2)
        args, expected = test[0], test[1]
        func = test[2] if len(test) > 2 else problem["function"]
        if timeout_spent >= TIMEOUT_BUDGET:
            out = {"passed": False, "actual": None, "lines": [],
                   "error": "Skipped: too many tests timed out"}
        else:
            started = time.monotonic()
            out = _run_one(code, func, args, expected, trace, PER_TEST_TIMEOUT,
                           problem.get("prelude", ""), problem.get("strict_types", True),
                           problem.get("check_mutation", False))
            if (out["error"] or "").startswith("Timed out"):
                timeout_spent += time.monotonic() - started
        entry = {"input": args, "expected": expected, "actual": out["actual"],
                 "passed": bool(out["passed"]), "error": out["error"]}
        if len(test) > 2:
            entry["function"] = func
        if out.get("note"):
            entry["note"] = out["note"]
        if trace:
            entry["lines"] = out["lines"]
        result["tests"].append(entry)

    result["num_passed"] = sum(t["passed"] for t in result["tests"])
    result["status"] = "passed" if result["num_passed"] == result["num_total"] else "failed"
    return result


def failure_kind(result):
    """Why a run_code() result failed, in one word. Useful for the hint writer and for reports:
       passes_all | syntax_error | timeout (infinite loop) | runtime_error (crash) | wrong_answer
    """
    if result["status"] == "passed":
        return "passes_all"
    if result["status"] == "syntax_error":
        return "syntax_error"
    errors = [t["error"] or "" for t in result["tests"] if not t["passed"]]
    if any("Timed out" in e or e.startswith("Skipped:") for e in errors):
        return "timeout"
    if any(errors):
        return "runtime_error"
    return "wrong_answer"


if __name__ == "__main__":  # quick manual use:  python runner.py sum_list file.py
    res = run_code(open(sys.argv[2]).read(), sys.argv[1])
    print(json.dumps(res, indent=2))
