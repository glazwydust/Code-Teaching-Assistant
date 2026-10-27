"""Load the Refactory / NUS Singapore dataset as codeEd problems.

Dataset layout (from the Refactory repo, unzipped from data.zip):

    data/question_N/description.txt
    data/question_N/ans/input_001.txt     e.g.  search(42, (-5, 1, 3, 5, 7, 10))
    data/question_N/ans/output_001.txt    e.g.  6
    data/question_N/code/reference/reference.py   instructor's correct solution
    data/question_N/code/correct/*.py              student programs that pass every test
    data/question_N/code/wrong/*.py                student programs that fail at least one
    data/question_N/code/global.py                 optional shared imports/globals

load_question() turns one question into the same dict shape as problems/*.py, so
run_code(code, problem) and find_suspicious_lines(code, problem) work on it directly
(pass the dict instead of a problem id).

If every test calls the same function, tests are (args, expected) and problem["function"] is
that function. If tests call several functions (question 2 has three), each test is
(args, expected, function_name) and problem["functions"] lists them.
No bug-type labels exist in this dataset: it only says "correct" or "wrong".
Cite: Hu, Ahmed, Mechtaev, Leong, Roychoudhury, "Re-factoring based Program Repair applied
to Programming Assignments", ASE 2019.
"""
import ast
import glob
import os
import random


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _global_names(path):
    """Variables defined in the question's global.py (e.g. tuple_of_possible_birthdays)."""
    ns = {}
    if os.path.exists(path):
        try:
            exec(compile(_read(path), path, "exec"), ns)
        except Exception:  # noqa: BLE001 - a broken global.py just means no names
            ns = {}
    ns.pop("__builtins__", None)
    return ns


def _arg_value(node, names):
    """A written-out literal, or a plain variable name defined in global.py."""
    try:
        return ast.literal_eval(node)
    except ValueError:
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        raise


def parse_call(text, names=None):
    """'search(42, (-5, 1))'  ->  ('search', [42, (-5, 1)])
    Arguments may also be variable names defined in global.py (pass them in `names`)."""
    node = ast.parse(text.strip(), mode="eval").body
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)) or node.keywords:
        raise ValueError("not a plain function call")
    return node.func.id, [_arg_value(arg, names or {}) for arg in node.args]


def load_question(data_dir, q):
    qdir = os.path.join(data_dir, f"question_{q}")
    ans = os.path.join(qdir, "ans")
    prelude_path = os.path.join(qdir, "code", "global.py")
    global_names = _global_names(prelude_path)
    triples, skipped = [], 0
    for in_path in sorted(glob.glob(os.path.join(ans, "input_*.txt"))):
        out_path = os.path.join(ans, "output_" + os.path.basename(in_path)[len("input_"):])
        try:
            name, args = parse_call(_read(in_path), global_names)
            expected = ast.literal_eval(_read(out_path).strip())
        except (ValueError, SyntaxError, TypeError, OSError):
            skipped += 1
            continue
        triples.append((args, expected, name))
    names = sorted({t[2] for t in triples})
    if not names:
        raise ValueError(f"question_{q}: no usable tests found ({skipped} tests skipped; each input "
                         "must be a plain call like f(1, [2]) and each output a Python literal)")
    if len(names) == 1:
        tests = [(a, e) for a, e, _ in triples]
    else:
        tests = triples                      # (args, expected, function_name)

    description = _read(os.path.join(qdir, "description.txt")).strip()
    reference = _read(os.path.join(qdir, "code", "reference", "reference.py"))
    return {
        "id": f"nus_q{q}",
        "function": names[0],
        "functions": names,
        "strict_types": True,            # value AND top-level type must match; False = plain ==
        "title": description.splitlines()[0] if description else f"NUS question {q}",
        "description": description,
        "tests": tests,
        "solutions": [reference],
        "buggy_examples": [],            # no bug-type labels in this dataset
        "prelude": _read(prelude_path) if os.path.exists(prelude_path) else "",
        "dir": qdir,
        "skipped_tests": skipped,
    }


def list_programs(problem, kind, limit=None, seed=0):
    """Paths of student programs. kind is 'correct' or 'wrong'. limit gives a random sample."""
    paths = sorted(glob.glob(os.path.join(problem["dir"], "code", kind, "*.py")))
    if limit and len(paths) > limit:
        paths = sorted(random.Random(seed).sample(paths, limit))
    return paths


def program_code(path):
    return _read(path)
