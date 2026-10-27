"""Sandboxed worker: runs ONE test of the student's code in its own process.

Why a separate process? A crash, an infinite loop or a memory blow-up in student code
must never take down the website. The parent (runner.py) kills this process on timeout.

Input  (JSON on stdin):  {code, func, args_repr, expected_repr, trace, prelude}
    args_repr / expected_repr are Python literal text (repr), read back with ast.literal_eval
    so that tuples stay tuples. (Old keys 'args' / 'expected' still work.)
    prelude is optional setup code (imports/globals) run before the student's code.
Output (JSON on stdout): {passed, actual, error, lines}
"""
import ast
import copy
import io
import json
import sys

try:  # cap memory (Linux/macOS); ignore if unavailable
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
except Exception:
    pass

STUDENT_FILE = "<student>"


def jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def main():
    real_stdout = sys.stdout
    payload = json.loads(sys.stdin.read())
    out = {"passed": False, "actual": None, "error": None, "lines": []}
    seen = set()

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != STUDENT_FILE:
            return None
        if event == "line":
            seen.add(frame.f_lineno)
        return tracer

    sys.stdout = io.StringIO()  # swallow student print() so our JSON stays clean
    try:
        namespace = {"__name__": "student_module"}
        if payload.get("prelude"):
            exec(compile(payload["prelude"], "<prelude>", "exec"), namespace)
        exec(compile(payload["code"], STUDENT_FILE, "exec"), namespace)
        func = namespace.get(payload["func"])
        if not callable(func):
            out["error"] = f"Function '{payload['func']}' is not defined"
        else:
            if "args_repr" in payload:
                args = ast.literal_eval(payload["args_repr"])
                expected = ast.literal_eval(payload["expected_repr"])
            else:
                args = copy.deepcopy(payload["args"])
                expected = payload["expected"]
            before = copy.deepcopy(args) if payload.get("check_mutation") else None
            if payload.get("trace"):
                sys.settrace(tracer)
            try:
                actual = func(*args)
            finally:
                sys.settrace(None)
            out["actual"] = jsonable(actual)
            # Same value AND same type, so True != 1 and None != False.
            strict = payload.get("strict_types", True)  # False = compare values only
            out["passed"] = (actual == expected) and (not strict or type(actual) is type(expected))
            if before is not None and args != before:   # optional check: did it change its input?
                out["passed"] = False
                out["note"] = "the function modified its input"
    except BaseException as exc:  # noqa: BLE001 - we want every student error
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout
        out["lines"] = sorted(seen)
    real_stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
