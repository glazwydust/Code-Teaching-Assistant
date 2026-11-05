"""Auto-label the bug types of the real NUS wrong programs, by repair search.

Idea: every bug type has small edits that would FIX it (the inverse of the mutation P2 uses
to make training data). We try each small edit on a wrong program and run the tests.
If one edit makes every test pass, that edit tells us the bug type and the line, with
proof. If one edit is not enough, we try two in a row (programs with two bugs).
If nothing fixes it, we fall back to clues from how it fails (NameError -> name_typo,
returns None -> missing_return, only empty inputs fail -> missing_edge_case, ...).

    confidence = confirmed   an edit (or two) made every test pass
                 likely      no fix found; label comes from failure clues or a partial fix
                 unknown     nothing to go on -> "other"

Run from the codeEd folder (one command):

    py scripts/auto_label.py --out auto_labels.csv            # all 5 questions, ~10-20 min
    py scripts/auto_label.py --questions 1 --limit 50         # quick try
    py scripts/auto_label.py --compare labels_real_50.csv     # also score against human labels

The labels are machine-made. Use them for training data and for the "what do real students
get wrong" analysis. Keep the human-labelled set as the final test set.
"""
import argparse
import warnings
warnings.filterwarnings("ignore")
import ast
import copy
import csv
import difflib
import io
import multiprocessing as mp
import os
import signal
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nus  # noqa: E402
from problems import nus_data_dir  # noqa: E402

# When several different edits each fix the program, report the simplest explanation first.
PRIORITY = ["name_typo", "missing_return", "missing_edge_case", "wrong_operator", "off_by_one",
            "wrong_init", "wrong_variable", "early_return", "missing_base_case", "other"]

PER_TEST_SECONDS = 0.05      # correct answers run in microseconds; anything slower is a loop
PROGRAM_SECONDS = 120        # hard cap for the whole search on one program
INPLACE = {"sort", "reverse", "append", "extend", "insert", "remove", "clear"}


# ----------------------------------------------------------------------------- running code
class _Timeout(BaseException):
    pass


def _alarm(signum, frame):
    raise _Timeout()


class _Sink(io.StringIO):
    def write(self, s):
        return len(s)


def evaluate(code, problem, detail=False):
    """Run code on every test in this process. Returns (num_passed, details or None).
    details: list of (passed, actual_repr, error_type) per test."""
    tests = problem["tests"]
    try:
        compiled = compile(code, "<student>", "exec")
    except (SyntaxError, ValueError):
        return 0, ([(False, None, "SyntaxError")] * len(tests) if detail else None)
    real_out, real_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = _Sink(), io.StringIO("")
    passed, details = 0, []
    ns = None
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, PER_TEST_SECONDS * 4)
        try:
            ns = {"__name__": "student_module"}
            if problem.get("prelude"):
                exec(problem["prelude"], ns)
            exec(compiled, ns)
        except BaseException as exc:  # noqa: BLE001
            ns = ("module", type(exc).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        timed_out = False
        for test in tests:
            args, expected = test[0], test[1]
            fname = test[2] if len(test) > 2 else problem["function"]
            if isinstance(ns, tuple):
                details.append((False, None, ns[1]))
                continue
            if timed_out:
                details.append((False, None, "Timeout"))
                continue
            func = ns.get(fname)
            if not callable(func):
                details.append((False, None, "MissingFunction"))
                continue
            ok, actual, err = False, None, None
            signal.setitimer(signal.ITIMER_REAL, PER_TEST_SECONDS)
            try:
                actual = func(*copy.deepcopy(args))
                signal.setitimer(signal.ITIMER_REAL, 0)
                try:
                    ok = bool(actual == expected) and type(actual) is type(expected)
                except Exception:  # noqa: BLE001
                    ok = False
            except _Timeout:
                err, timed_out = "Timeout", True
            except BaseException as exc:  # noqa: BLE001
                err = type(exc).__name__
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            passed += ok
            if detail:
                try:
                    a = repr(actual)[:60]
                except Exception:  # noqa: BLE001
                    a = "<unprintable>"
                details.append((ok, a, err))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        sys.stdout, sys.stdin = real_out, real_in
    return passed, (details if detail else None)


# ----------------------------------------------------------------------------- candidate edits
def _src(node, limit=70):
    try:
        s = ast.unparse(node).replace("\n", "; ")
    except Exception:  # noqa: BLE001
        s = type(node).__name__
    return s if len(s) <= limit else s[:limit - 3] + "..."


def _parents(tree):
    par = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            par[c] = p
    return par


def _contains_len_or_int(node):
    def is_count(o):
        if isinstance(o, ast.Call) and isinstance(o.func, ast.Name) and o.func.id == "len":
            return True
        if isinstance(o, ast.Constant) and type(o.value) is int:
            return True
        if isinstance(o, ast.BinOp):
            return is_count(o.left) or is_count(o.right)
        return False
    # comparing a count (len(x) <= k, i < 3) is off-by-one; comparing values (x < seq[i]) is not
    return any(is_count(o) for o in [node.left] + node.comparators)


CMP = [ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq]
BOUNDARY = {(ast.Lt, ast.LtE), (ast.LtE, ast.Lt), (ast.Gt, ast.GtE), (ast.GtE, ast.Gt)}


def _func_of(node, par):
    while node in par:
        node = par[node]
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            return node
    return None


def _scope_names(fn):
    names = set()
    if isinstance(fn, ast.FunctionDef):
        names |= {a.arg for a in fn.args.args}
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
    return names


def _empty_values(problem, fname, pidx):
    """Expected outputs of tests where argument pidx of fname is empty."""
    vals = []
    for t in problem["tests"]:
        f = t[2] if len(t) > 2 else problem["function"]
        if f != fname or pidx >= len(t[0]):
            continue
        a = t[0][pidx]
        if hasattr(a, "__len__") and len(a) == 0 and repr(t[1]) not in [repr(v) for v in vals]:
            vals.append(t[1])
    return vals


def candidates(code, problem):
    """All single-edit repairs as dicts {label, fine, line, desc, code}."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    nodes = list(ast.walk(tree))
    par = _parents(tree)
    out, seen = [], {code}
    defined_funcs = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
    module_defs = set(defined_funcs) | {
        t.id for n in nodes if isinstance(n, (ast.Assign, ast.For)) for t in ast.walk(n)
        if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
    for alias in (a for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names):
        module_defs.add((alias.asname or alias.name).split(".")[0])
    prelude_names = set()
    if problem.get("prelude"):
        try:
            for n in ast.walk(ast.parse(problem["prelude"])):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    prelude_names.add(n.id)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        prelude_names.add((a.asname or a.name).split(".")[0])
        except SyntaxError:
            pass
    import builtins
    builtin_names = set(dir(builtins))

    def add(label, fine, idx, fn, before=None, after=None):
        t2 = copy.deepcopy(tree)
        n2 = list(ast.walk(t2))[idx]
        try:
            res = fn(n2, t2)
            if res is False:
                return
            ast.fix_missing_locations(t2)
            new = ast.unparse(t2)
            compile(new, "<c>", "exec")
        except Exception:  # noqa: BLE001
            return
        if new in seen:
            return
        seen.add(new)
        node = nodes[idx]
        line = getattr(node, "lineno", 0)
        b = before if before is not None else _src(node)
        a = after if after is not None else _src(n2)
        out.append({"label": label, "fine": fine, "line": line, "desc": f"L{line}: {b} -> {a}",
                    "code": new})

    for idx, node in enumerate(nodes):
        # -- comparison operators
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                cur = type(op)
                alts = []
                if cur in CMP:
                    alts = [c for c in CMP if c is not cur]
                elif cur is ast.In:
                    alts = [ast.NotIn]
                elif cur is ast.NotIn:
                    alts = [ast.In]
                elif cur is ast.Is:
                    alts = [ast.Eq, ast.IsNot]
                elif cur is ast.IsNot:
                    alts = [ast.NotEq, ast.Is]
                for alt in alts:
                    if (cur, alt) in BOUNDARY:
                        lab = "off_by_one" if _contains_len_or_int(node) else "wrong_operator"
                        fine = "boundary_compare"
                    else:
                        lab, fine = "wrong_operator", "compare_op"

                    def f(n, t, k=k, alt=alt):
                        n.ops[k] = alt()
                    add(lab, fine, idx, f)
        # -- arithmetic / boolean operators
        if isinstance(node, ast.BinOp):
            swaps = {ast.Add: [ast.Sub], ast.Sub: [ast.Add], ast.Mult: [ast.FloorDiv, ast.Add],
                     ast.FloorDiv: [ast.Mult, ast.Div], ast.Div: [ast.FloorDiv]}
            for alt in swaps.get(type(node.op), []):
                def f(n, t, alt=alt):
                    n.op = alt()
                add("wrong_operator", "arith_op", idx, f)
            # x + 1 / x - 1  ->  x   (off by one)
            for side, other in (("right", "left"), ("left", "right")):
                c = getattr(node, side)
                if isinstance(node.op, (ast.Add, ast.Sub)) and isinstance(c, ast.Constant) \
                        and c.value == 1:
                    def f(n, t, other=other):
                        p = _parents(t)[n]
                        for field, val in ast.iter_fields(p):
                            if val is n:
                                setattr(p, field, getattr(n, other))
                                return
                            if isinstance(val, list) and n in val:
                                val[val.index(n)] = getattr(n, other)
                                return
                        return False
                    add("off_by_one", "drop_plus_minus_one", idx, f)
        if isinstance(node, ast.BoolOp):
            def f(n, t):
                n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
            add("wrong_operator", "and_or", idx, f)
        if isinstance(node, (ast.If, ast.While)):
            def f(n, t):
                n.test = n.test.operand if isinstance(n.test, ast.UnaryOp) and \
                    isinstance(n.test.op, ast.Not) else ast.UnaryOp(ast.Not(), n.test)
            add("wrong_operator", "negate_condition", idx, f, _src(node.test),
                "not (" + _src(node.test) + ")")
        # -- integer constants (not initialisers) +-1
        if isinstance(node, ast.Constant) and type(node.value) is int:
            p = par.get(node)
            is_init = isinstance(p, ast.Assign) and _func_of(node, par) is not None
            if not is_init:
                for d in (1, -1):
                    def f(n, t, d=d):
                        n.value = n.value + d
                    add("off_by_one", "constant_pm1", idx, f)
            fn = _func_of(node, par)
            if not is_init and isinstance(fn, ast.FunctionDef):
                for a in fn.args.args:
                    def f(n, t, a=a.arg):
                        p = _parents(t)[n]
                        for field, val in ast.iter_fields(p):
                            if val is n:
                                setattr(p, field, ast.Name(a, ast.Load()))
                                return
                            if isinstance(val, list) and n in val:
                                val[val.index(n)] = ast.Name(a, ast.Load())
                                return
                        return False
                    add("wrong_variable", "hardcoded_constant", idx, f)
        # -- len(x) +- 1, index +- 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
            for op in (ast.Sub, ast.Add):
                def f(n, t, op=op):
                    p = _parents(t)[n]
                    new = ast.BinOp(copy.deepcopy(n), op(), ast.Constant(1))
                    for field, val in ast.iter_fields(p):
                        if val is n:
                            setattr(p, field, new)
                            return
                        if isinstance(val, list) and n in val:
                            val[val.index(n)] = new
                            return
                    return False
                add("off_by_one", "len_pm1", idx, f)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name):
            for op in (ast.Add, ast.Sub):
                def f(n, t, op=op):
                    n.slice = ast.BinOp(n.slice, op(), ast.Constant(1))
                add("off_by_one", "index_pm1", idx, f)
        # -- initialisers
        if isinstance(node, ast.Assign) and _func_of(node, par) is not None:
            v = node.value
            alts = []
            if isinstance(v, ast.Constant):
                if type(v.value) is bool:
                    alts = [not v.value]
                elif type(v.value) is int:
                    alts = sorted({v.value + 1, v.value - 1, 0, 1, -1} - {v.value})
                elif v.value is None:
                    alts = [0, False, "[]"]
            elif isinstance(v, (ast.List, ast.Tuple)) and not v.elts:
                alts = ["[]" if isinstance(v, ast.Tuple) else "()"]
            for alt in alts:
                def f(n, t, alt=alt):
                    n.value = ast.parse(alt, mode="eval").body if isinstance(alt, str) \
                        else ast.Constant(alt)
                add("wrong_init", "init_value", idx, f)
            # initialiser placed inside a loop -> move it before the loop
            loop = par.get(node)
            if isinstance(v, (ast.Constant, ast.List, ast.Tuple)) and isinstance(loop, (ast.For, ast.While)) \
                    and node in loop.body:
                def f(n, t):
                    p = _parents(t)
                    lp = p[n]
                    holder = p[lp]
                    for field, val in ast.iter_fields(holder):
                        if isinstance(val, list) and lp in val:
                            lp.body.remove(n)
                            if not lp.body:
                                lp.body.append(ast.Pass())
                            val.insert(val.index(lp), n)
                            return
                    return False
                add("wrong_init", "init_inside_loop", idx, f, _src(node), "(moved before loop)")
        # -- missing return: print(x) -> return x
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and \
                isinstance(node.value.func, ast.Name) and node.value.func.id == "print" and \
                len(node.value.args) == 1 and _func_of(node, par) is not None:
            def f(n, t):
                p = _parents(t)[n]
                for field, val in ast.iter_fields(p):
                    if isinstance(val, list) and n in val:
                        val[val.index(n)] = ast.Return(n.value.args[0])
                        return
                return False
            add("missing_return", "print_instead_of_return", idx, f, _src(node), "return " + _src(node.value.args[0]))
        # -- return lst.sort() / return lst.reverse  -> call, then return lst
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            call = v if isinstance(v, ast.Call) else None
            attr = call.func if call is not None else v
            if isinstance(attr, ast.Attribute) and attr.attr in INPLACE:
                def f(n, t):
                    v2 = n.value
                    c2 = v2 if isinstance(v2, ast.Call) else ast.Call(v2, [], [])
                    target = c2.func.value
                    p = _parents(t)[n]
                    for field, val in ast.iter_fields(p):
                        if isinstance(val, list) and n in val:
                            i = val.index(n)
                            val[i:i + 1] = [ast.Expr(c2), ast.Return(copy.deepcopy(target))]
                            return
                    return False
                add("missing_return", "return_of_inplace_method", idx, f, _src(node), "call it, then return " + _src(attr.value))
        # -- x = x.append(i)  (assigns None)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and \
                isinstance(node.value.func, ast.Attribute) and node.value.func.attr in INPLACE:
            def f(n, t):
                p = _parents(t)[n]
                for field, val in ast.iter_fields(p):
                    if isinstance(val, list) and n in val:
                        val[val.index(n)] = ast.Expr(n.value)
                        return
                return False
            add("other", "assign_result_of_inplace_method", idx, f, _src(node), _src(node.value))
        # -- lst += x / lst = lst + x where x is an item -> append
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and \
                isinstance(node.target, ast.Name):
            def f(n, t):
                p = _parents(t)[n]
                new = ast.Expr(ast.Call(ast.Attribute(ast.Name(n.target.id, ast.Load()), "append",
                                                      ast.Load()), [n.value], []))
                for field, val in ast.iter_fields(p):
                    if isinstance(val, list) and n in val:
                        val[val.index(n)] = new
                        return
                return False
            add("other", "plus_instead_of_append", idx, f, _src(node),
                f"{node.target.id}.append({_src(node.value)})")
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.BinOp) and \
                isinstance(node.value.op, ast.Add) and isinstance(node.value.left, ast.Name) and \
                node.value.left.id == node.targets[0].id:
            def f(n, t):
                p = _parents(t)[n]
                new = ast.Expr(ast.Call(ast.Attribute(ast.Name(n.targets[0].id, ast.Load()),
                                                      "append", ast.Load()), [n.value.right], []))
                for field, val in ast.iter_fields(p):
                    if isinstance(val, list) and n in val:
                        val[val.index(n)] = new
                        return
                return False
            add("other", "plus_instead_of_append", idx, f, _src(node),
                f"{node.targets[0].id}.append({_src(node.value.right)})")
        # -- obj.append[x] -> obj.append(x)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            def f(n, t):
                p = _parents(t)[n]
                sl = n.slice
                args = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
                new = ast.Call(n.value, args, [])
                for field, val in ast.iter_fields(p):
                    if val is n:
                        setattr(p, field, new)
                        return
                    if isinstance(val, list) and n in val:
                        val[val.index(n)] = new
                        return
                return False
            add("name_typo", "brackets_instead_of_call", idx, f)
        # -- names: typos (undefined) and wrong variable (defined, but the wrong one)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            fn = _func_of(node, par)
            scope = _scope_names(fn) if fn is not None else set()
            known = scope | module_defs | prelude_names
            is_callee = isinstance(par.get(node), ast.Call) and par[node].func is node
            if node.id not in known and node.id not in builtin_names:
                pool = sorted(known | (builtin_names if is_callee else set()))
                for alt in difflib.get_close_matches(node.id, pool, n=3, cutoff=0.5) or \
                        sorted(scope)[:6]:
                    def f(n, t, alt=alt):
                        n.id = alt
                    add("name_typo", "undefined_name", idx, f)
            elif fn is not None and not is_callee and node.id in scope:
                for alt in sorted(scope - {node.id} - defined_funcs):
                    def f(n, t, alt=alt):
                        n.id = alt
                    add("wrong_variable", "swap_variable", idx, f)
        # -- attribute typos: lst.apend -> lst.append
        if isinstance(node, ast.Attribute) and isinstance(par.get(node), ast.Call):
            methods = set(dir(list)) | set(dir(str)) | set(dir(dict)) | set(dir(tuple))
            if node.attr not in methods:
                for alt in difflib.get_close_matches(node.attr, sorted(methods), n=2, cutoff=0.6):
                    def f(n, t, alt=alt):
                        n.attr = alt
                    add("name_typo", "method_typo", idx, f)
        # -- function-level edits
        if isinstance(node, ast.FunctionDef):
            has_value_return = any(isinstance(n, ast.Return) and n.value is not None
                                   for n in ast.walk(node))
            last = node.body[-1] if node.body else None
            if not isinstance(last, ast.Return):
                names = [a.arg for a in node.args.args] + sorted(
                    _scope_names(node) - {a.arg for a in node.args.args})
                for name in names:
                    def f(n, t, name=name):
                        n.body.append(ast.Return(ast.Name(name, ast.Load())))
                    lab = "missing_return" if not has_value_return else "missing_return"
                    add(lab, "no_return_at_end", idx, f, "(end of " + node.name + ")",
                        "return " + name)
            # empty-input guard
            for pidx, a in enumerate(node.args.args):
                for val in _empty_values(problem, node.name, pidx):
                    for guard in (f"if not {a.arg}:\n    return {val!r}",):
                        def f(n, t, guard=guard):
                            n.body.insert(0, ast.parse(guard).body[0])
                        add("missing_edge_case", "empty_input_guard", idx, f,
                            "(start of " + node.name + ")", guard.replace("\n   ", ""))
        # -- return inside the loop (returns on the first item) -> move it after the loop
        if isinstance(node, (ast.For, ast.While)):
            rets = []
            for st in node.body:
                if isinstance(st, ast.Return):
                    rets.append(("body", st))
                if isinstance(st, ast.If):
                    for st2 in st.orelse:
                        if isinstance(st2, ast.Return):
                            rets.append(("else", st2))
            for where, r in rets:
                ridx = nodes.index(r)

                def f(n, t, ridx=ridx):
                    allnodes = list(ast.walk(t))
                    r2 = allnodes[ridx]
                    p = _parents(t)
                    holder = p[r2]
                    lst = holder.orelse if r2 in getattr(holder, "orelse", []) else holder.body
                    lst.remove(r2)
                    if not lst and lst is holder.body:
                        lst.append(ast.Pass())
                    lp = n
                    outer = p[lp]
                    for field, val in ast.iter_fields(outer):
                        if isinstance(val, list) and lp in val:
                            val.insert(val.index(lp) + 1, r2)
                            return
                    return False
                add("early_return", "return_inside_loop", idx, f, _src(r), "(moved after the loop)")
    return out


# ----------------------------------------------------------------------------- the search
def _is_empty_test(test):
    return any(hasattr(a, "__len__") and len(a) == 0 for a in test[0])


def _only_empty_fail(problem, details):
    fails = [i for i, d in enumerate(details) if not d[0]]
    return bool(fails) and all(_is_empty_test(problem["tests"][i]) for i in fails)


def _changes_list_while_looping(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for loop in ast.walk(tree):
        if isinstance(loop, ast.For) and isinstance(loop.iter, ast.Name):
            for n in ast.walk(loop):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
                        n.func.attr in ("remove", "pop", "insert", "append") and \
                        isinstance(n.func.value, ast.Name) and n.func.value.id == loop.iter.id:
                    return True
    return False


def clues(code, problem, details):
    """Labels from HOW the program fails, used when no repair is found."""
    labels, fine = [], []
    errs = [d[2] for d in details if not d[0] and d[2]]
    tests = problem["tests"]
    fails = [i for i, d in enumerate(details) if not d[0]]
    if errs and all(e == "SyntaxError" for e in errs):
        return ["other"], ["syntax_error"]
    if "NameError" in errs or "UnboundLocalError" in errs:
        labels.append("name_typo")
        fine.append("name_error")
    if "MissingFunction" in errs:
        labels.append("missing_return")
        fine.append("function_missing")
    none_fails = [i for i in fails if details[i][1] == "None" and not details[i][2]
                  and tests[i][1] is not None]
    if none_fails and len(none_fails) >= max(1, len(fails) // 2):
        labels.append("missing_return")
        fine.append("returns_none")
    if fails and all(_is_empty_test(tests[i]) for i in fails):
        labels.append("missing_edge_case")
        fine.append("only_empty_inputs_fail")
    if "RecursionError" in errs:
        labels.append("missing_base_case")
        fine.append("recursion_error")
    if "Timeout" in errs:
        fine.append("infinite_loop")
    if _changes_list_while_looping(code):
        fine.append("changes_list_while_looping")
    if "TypeError" in errs:
        fine.append("type_error")
    if "IndexError" in errs:
        fine.append("index_error")
    return labels, fine


# How natural each kind of edit is (lower = the more likely explanation when several edits fix it).
FINE_COST = {"undefined_name": 0, "brackets_instead_of_call": 0, "method_typo": 0,
             "print_instead_of_return": 1, "return_of_inplace_method": 1, "no_return_at_end": 1,
             "assign_result_of_inplace_method": 1, "plus_instead_of_append": 1,
             "boundary_compare": 1, "drop_plus_minus_one": 1, "empty_input_guard": 2,
             "compare_op": 2, "constant_pm1": 2, "len_pm1": 2, "index_pm1": 2, "init_value": 2,
             "init_inside_loop": 2, "arith_op": 2, "and_or": 2, "return_inside_loop": 2,
             "swap_variable": 3, "hardcoded_constant": 3, "negate_condition": 4}
BEAM = [None, 6, 3]   # programs kept after 1 and 2 edits


def _cost(path):
    return (sum(FINE_COST.get(c["fine"], 3) for c in path),
            min(PRIORITY.index(c["label"]) for c in path))


def label_program(code, problem):
    n_tests = len(problem["tests"])
    base, details = evaluate(code, problem, detail=True)
    errs = [d[2] for d in details if not d[0] and d[2]]
    if any(e == "Timeout" for e in errs):
        kind = "timeout"
    elif errs:
        kind = "runtime_error"
    else:
        kind = "wrong_answer"
    res = {"failure_kind": kind, "passed": base, "total": n_tests}
    if base == n_tests:
        res.update(labels="passes_tests", primary="passes_tests", confidence="n/a",
                   fine="passes_tests", fix="", alt_labels="")
        return res
    try:
        ast.parse(code)
    except SyntaxError:
        res.update(failure_kind="syntax_error", labels="other", primary="other",
                   confidence="likely", fine="syntax_error", fix="", alt_labels="")
        return res

    # Beam search: up to 3 edits; keep the programs that pass more tests than before.
    frontier = [(base, code, [])]
    fixes, best_partial, seen = [], None, {code}
    for depth in range(3):
        grown = []
        for p0, c0, path in frontier:
            for c in candidates(c0, problem):
                if c["code"] in seen:
                    continue
                seen.add(c["code"])
                p, _ = evaluate(c["code"], problem)
                if p == n_tests:
                    fixes.append(path + [c])
                elif p > p0:
                    grown.append((p, c["code"], path + [c]))
                    if depth == 0 and (best_partial is None or
                                       (p, -FINE_COST.get(c["fine"], 3)) >
                                       (best_partial[0], -FINE_COST.get(best_partial[1]["fine"], 3))):
                        best_partial = (p, c)
        if fixes or depth == 2:
            break
        grown.sort(key=lambda g: (-g[0], _cost(g[2])))
        frontier = grown[:BEAM[depth + 1]]
        if not frontier:
            break
    if fixes:
        fixes.sort(key=lambda path: _cost(path))
        best = fixes[0]
        labels = sorted({c["label"] for c in best}, key=PRIORITY.index)
        others = sorted({c["label"] for path in fixes[1:6] for c in path} - set(labels),
                        key=PRIORITY.index)
        if _only_empty_fail(problem, details) and all(
                c["fine"] in ("no_return_at_end", "empty_input_guard", "constant_pm1",
                              "len_pm1", "drop_plus_minus_one") for c in best):
            labels = ["missing_edge_case"]
        res.update(labels=";".join(labels), primary=labels[0], confidence="confirmed",
                   fine=";".join(c["fine"] for c in best),
                   fix=" | ".join(c["desc"] for c in best), alt_labels=";".join(others))
        return res
    labs, fine = clues(code, problem, details)
    fix = ""
    if best_partial is not None:
        p, top = best_partial
        if top["label"] not in labs:
            labs.append(top["label"])
        fine.append("partial:" + top["fine"])
        fix = f"partial fix ({p}/{n_tests}): " + top["desc"]
    labs = sorted(set(labs), key=PRIORITY.index)
    res.update(labels=";".join(labs) if labs else "other", primary=labs[0] if labs else "other",
               confidence="likely" if labs else "unknown", fine=";".join(fine), fix=fix,
               alt_labels="")
    return res


# ----------------------------------------------------------------------------- driver
_PROBLEMS = {}


def _child(job, conn):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    sys.setrecursionlimit(400)
    q, path = job
    try:
        out = label_program(nus.program_code(path), _PROBLEMS[q])
    except BaseException as exc:  # noqa: BLE001
        out = {"failure_kind": "?", "passed": "", "total": "", "labels": "other",
               "primary": "other", "confidence": "unknown", "fine": "labeller_error:" +
               type(exc).__name__, "fix": "", "alt_labels": ""}
    try:
        conn.send(out)
    finally:
        conn.close()


def run_all(jobs, workers):
    ctx = mp.get_context("fork")
    results, active = {}, {}
    pending = list(jobs)
    done = 0
    while pending or active:
        while pending and len(active) < workers:
            job = pending.pop(0)
            r, w = ctx.Pipe(False)
            p = ctx.Process(target=_child, args=(job, w))
            p.start()
            w.close()
            active[p] = (job, r, time.time())
        for p, (job, r, t0) in list(active.items()):
            out = None
            if r.poll():
                try:
                    out = r.recv()
                except EOFError:
                    out = None
                p.join(1)
            elif not p.is_alive():
                out = None
            elif time.time() - t0 > PROGRAM_SECONDS:
                p.kill()
            else:
                continue
            if out is None:
                out = {"failure_kind": "?", "passed": "", "total": "", "labels": "other",
                       "primary": "other", "confidence": "unknown", "fine": "labeller_crash",
                       "fix": "", "alt_labels": ""}
            results[job] = out
            p.join(1)
            del active[p]
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)}", flush=True)
        time.sleep(0.003)
    return results


def compare(rows, human_csv):
    by_file = {r["file"]: r for r in rows}
    agree_exact = agree_primary = agree_any = n = 0
    per = Counter()
    lines = []
    with open(human_csv, encoding="utf-8") as f:
        for h in csv.DictReader(f):
            a = by_file.get(h["file"])
            if not a:
                continue
            n += 1
            hs = {x.strip() for x in h["label"].split(";") if x.strip()}
            as_ = {x for x in a["labels"].split(";") if x}
            agree_exact += hs == as_
            agree_any += bool(hs & as_)
            agree_primary += a["primary"] in hs
            for lab in hs:
                per[(lab, "human")] += 1
                per[(lab, "both")] += lab in as_
            lines.append((h["file"], h["label"], a["labels"], a["confidence"], a["fix"][:90]))
    print(f"\nAgreement with {human_csv} ({n} programs):")
    print(f"  exact same label set : {agree_exact}/{n}")
    print(f"  main label is one of the human labels: {agree_primary}/{n}")
    print(f"  at least one label in common: {agree_any}/{n}")
    print("  per human label (found by auto / given by human):")
    for lab in PRIORITY:
        if per[(lab, "human")]:
            print(f"    {lab:18s} {per[(lab, 'both')]}/{per[(lab, 'human')]}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--limit", type=int, default=0, help="programs per question (0 = all)")
    ap.add_argument("--files", nargs="*", help="only these file names")
    ap.add_argument("--out", default="auto_labels.csv")
    ap.add_argument("--compare", help="human label CSV (question,file,...,label)")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    args = ap.parse_args()

    jobs = []
    for q in args.questions:
        _PROBLEMS[q] = nus.load_question(nus_data_dir(), q)
        for path in nus.list_programs(_PROBLEMS[q], "wrong", limit=args.limit or None):
            if args.files and os.path.basename(path) not in args.files:
                continue
            jobs.append((q, path))
    print(f"labelling {len(jobs)} wrong programs with {args.workers} workers...", flush=True)
    t0 = time.time()
    results = run_all(jobs, args.workers)
    rows = []
    for q, path in jobs:
        r = results[(q, path)]
        rows.append({"question": q, "file": os.path.basename(path), **r})
    fields = ["question", "file", "failure_kind", "passed", "total", "primary", "labels",
              "confidence", "fine", "fix", "alt_labels"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"done in {time.time() - t0:.0f}s -> {args.out}")
    print("confidence:", dict(Counter(r["confidence"] for r in rows)))
    print("main label:", dict(Counter(r["primary"] for r in rows).most_common()))
    if args.compare:
        compare(rows, args.compare)


if __name__ == "__main__":
    main()
