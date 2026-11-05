"""P2: make labelled training data by putting known bugs into correct programs.

Real wrong programs have no bug labels, so we make our own: take a program that passes every
test, break it in one (or two) known ways, and keep the result only if the tests now fail.
Because we did the breaking, we know the label and the line.

    correct program --[inject bug]--> mutant --[run tests]--> keep if it fails --> dataset row

The injections are the reverse of the repairs in auto_label.py, and they follow the labelling
rules the team agreed on, so synthetic and real labels mean the same thing:

  missing_edge_case  delete the empty-input check (or make it return the wrong thing).
                     Kept only if an EMPTY input now fails.
  wrong_operator     swap an operator: == / !=, > / <, + / -, and / or, in / not in.
                     A boundary swap (< / <=) on VALUES (x < seq[i]) also counts here.
  off_by_one         change a count by one: range(n) -> range(n-1), len(x) -> len(x)+1, i -> i+1,
                     or a boundary swap on COUNTS (len(x) <= k).
  missing_return     delete the final return, or turn it into print(...), or return lst.sort().
  name_typo          misspell one use of a variable (count -> conut), so it is undefined.
  wrong_variable     use a different, existing variable in one place.
  wrong_init         change a starting value (count = 0 -> 1, [] -> ()), or move it into the loop.
  early_return       move the return after the loop INTO the loop, so it fires on the first item.
                     Kept only if a NON-EMPTY input fails (the agreed rule).
  missing_base_case  delete the base case of a recursive function (only exists for factorial).

Run from the codeEd folder (one command):

    py scripts/mutate.py                       # toy problems + all 5 NUS questions
    py scripts/mutate.py --questions 1 --limit 50     # quick try
    py scripts/mutate.py --out data/mutants.jsonl --double 0.25 --seed 0

Output: one JSON object per line (see FIELDS below) and a count table per bug type.
"""
import argparse
import ast
import copy
import io
import json
import keyword
import multiprocessing as mp
import os
import random
import sys
import time
import tokenize
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import nus  # noqa: E402
from problems import PROBLEM_IDS, get_problem, nus_data_dir  # noqa: E402
from auto_label import (_func_of, _parents, _scope_names, _contains_len_or_int,  # noqa: E402
                        _is_empty_test, evaluate, BOUNDARY, CMP)

BUG_TYPES = ["missing_edge_case", "wrong_operator", "off_by_one", "missing_return", "name_typo",
             "wrong_variable", "wrong_init", "early_return", "missing_base_case"]
SITES_TRIED = 6          # sites tried per bug type per program before giving up on that type
PROGRAM_SECONDS = 60
FIELDS = ["id", "problem_id", "base_id", "labels", "lines", "edits", "code", "dynamic"]


# ----------------------------------------------------------------------------- helpers
def _replace_in_parent(t, old, new):
    p = _parents(t)[old]
    for field, val in ast.iter_fields(p):
        if val is old:
            setattr(p, field, new)
            return True
        if isinstance(val, list) and old in val:
            if new is None:
                val.remove(old)
                if not val and field in ("body", "orelse") and not isinstance(p, ast.Module):
                    if field == "body":
                        val.append(ast.Pass())
            else:
                val[val.index(old)] = new
            return True
    return False


def _is_emptiness_test(test, params):
    """Does this condition check whether a parameter is empty?  not seq / len(seq) == 0 / seq == ()"""
    for n in ast.walk(test):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not) and \
                isinstance(n.operand, ast.Name) and n.operand.id in params:
            return True
        if isinstance(n, ast.Compare):
            parts = [n.left] + n.comparators
            names = [p for p in parts if isinstance(p, ast.Name) and p.id in params]
            lens = [p for p in parts if isinstance(p, ast.Call) and isinstance(p.func, ast.Name)
                    and p.func.id == "len"]
            empties = [p for p in parts if (isinstance(p, (ast.List, ast.Tuple)) and not p.elts)
                       or (isinstance(p, ast.Constant) and p.value == 0)]
            if (names or lens) and empties:
                return True
    return False


def _typo(name, taken, rng):
    options = []
    if len(name) >= 3:
        i = rng.randrange(1, len(name) - 1)
        options.append(name[:i] + name[i + 1:])              # drop a letter: count -> cont
        j = rng.randrange(0, len(name) - 1)
        options.append(name[:j] + name[j + 1] + name[j] + name[j + 2:])   # swap: count -> conut
    options += [name + "s", name[:-1] if len(name) > 1 else name + "_"]
    options = [o for o in options if o and o.isidentifier() and o not in taken
               and not keyword.iskeyword(o) and o != name]
    return rng.choice(options) if options else None


def normalize(code):
    """Tokens with names renamed v0, v1, ... in order of first use (spacing, comments and the
    student's choice of names ignored, but WHICH variable is used where still counts)."""
    import builtins
    keep = set(dir(builtins)) | set(keyword.kwlist)
    out, names = [], {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.NAME:
                out.append(tok.string if tok.string in keep else
                           names.setdefault(tok.string, f"v{len(names)}"))
            elif tok.type in (tokenize.OP, tokenize.NUMBER, tokenize.STRING):
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return code
    return " ".join(out)


# ----------------------------------------------------------------------------- injections
def injections(code, problem, rng):
    """Every possible single bug for this program: list of {label, line, edit, make()}.
    make() returns the broken code (or None if that edit does not apply)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    nodes = list(ast.walk(tree))
    par = _parents(tree)
    funcs = [n for n in nodes if isinstance(n, ast.FunctionDef)]
    func_names = {f.name for f in funcs}
    out = []

    def add(label, node, fn, edit, line=None):
        idx = nodes.index(node)

        def make(idx=idx, fn=fn):
            t2 = copy.deepcopy(tree)
            n2 = list(ast.walk(t2))[idx]
            try:
                if fn(n2, t2) is False:
                    return None
                ast.fix_missing_locations(t2)
                new = ast.unparse(t2)
                compile(new, "<m>", "exec")
            except Exception:  # noqa: BLE001
                return None
            return new
        out.append({"label": label, "line": line or getattr(node, "lineno", 0), "edit": edit,
                    "make": make})

    for node in nodes:
        fn_node = _func_of(node, par)
        in_func = fn_node is not None
        # ---- comparisons: wrong_operator / off_by_one (same rule as the labeller)
        if isinstance(node, ast.Compare) and in_func:
            for k, op in enumerate(node.ops):
                cur = type(op)
                if cur in CMP:
                    alts = [c for c in CMP if c is not cur]
                elif cur in (ast.In, ast.NotIn):
                    alts = [ast.NotIn if cur is ast.In else ast.In]
                else:
                    continue
                for alt in alts:
                    if (cur, alt) in BOUNDARY:
                        label = "off_by_one" if _contains_len_or_int(node) else "wrong_operator"
                    else:
                        label = "wrong_operator"

                    def f(n, t, k=k, alt=alt):
                        n.ops[k] = alt()
                    add(label, node, f, f"{type(op).__name__} -> {alt.__name__}")
        if isinstance(node, ast.BinOp) and in_func and isinstance(node.op, (ast.Add, ast.Sub)):
            def f(n, t):
                n.op = ast.Sub() if isinstance(n.op, ast.Add) else ast.Add()
            add("wrong_operator", node, f, "+ <-> -")
        if isinstance(node, ast.BoolOp) and in_func:
            def f(n, t):
                n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
            add("wrong_operator", node, f, "and <-> or")
        # ---- off by one
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and in_func:
            if node.func.id == "range" and node.args:
                for d, op in ((-1, ast.Sub), (1, ast.Add)):
                    def f(n, t, op=op):
                        n.args[-1] = ast.BinOp(n.args[-1], op(), ast.Constant(1))
                    add("off_by_one", node, f, f"range end {d:+d}")
            if node.func.id == "len" and not isinstance(par.get(node), ast.Call):
                for op in (ast.Sub, ast.Add):
                    def f(n, t, op=op):
                        return _replace_in_parent(t, n, ast.BinOp(copy.deepcopy(n), op(),
                                                                  ast.Constant(1)))
                    add("off_by_one", node, f, "len(x) +-1")
        if isinstance(node, ast.Subscript) and in_func and isinstance(node.slice, ast.Name):
            for op in (ast.Add, ast.Sub):
                def f(n, t, op=op):
                    n.slice = ast.BinOp(n.slice, op(), ast.Constant(1))
                add("off_by_one", node, f, "index +-1")
        if isinstance(node, ast.Constant) and type(node.value) is int and in_func and \
                not isinstance(par.get(node), ast.Assign):
            for d in (1, -1):
                def f(n, t, d=d):
                    n.value += d
                add("off_by_one", node, f, f"constant {d:+d}")
        # ---- wrong_init
        if isinstance(node, ast.Assign) and in_func and isinstance(par.get(node), ast.FunctionDef):
            v = node.value
            alts = []
            if isinstance(v, ast.Constant) and type(v.value) is int:
                alts = [ast.Constant(v.value + 1), ast.Constant(v.value - 1)]
            elif isinstance(v, ast.Constant) and type(v.value) is bool:
                alts = [ast.Constant(not v.value)]
            elif isinstance(v, ast.List) and not v.elts:
                alts = [ast.Tuple([], ast.Load()), ast.Constant(None)]
            elif isinstance(v, ast.Tuple) and not v.elts:
                alts = [ast.List([], ast.Load())]
            for alt in alts:
                def f(n, t, alt=alt):
                    n.value = copy.deepcopy(alt)
                add("wrong_init", node, f, f"init {ast.unparse(v)} -> {ast.unparse(alt)}")
            # move the starting value into the next loop (it resets every time round)
            body = par[node].body
            i = body.index(node)
            loop = next((s for s in body[i + 1:] if isinstance(s, (ast.For, ast.While))), None)
            if loop is not None and isinstance(v, (ast.Constant, ast.List, ast.Tuple)):
                li = body.index(loop)

                def f(n, t, li=li):
                    holder = _parents(t)[n]
                    lp = holder.body[li]
                    holder.body.remove(n)
                    lp.body.insert(0, n)
                add("wrong_init", node, f, "init moved inside loop")
        # ---- names: name_typo and wrong_variable
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and in_func:
            scope = _scope_names(fn_node)
            callee = isinstance(par.get(node), ast.Call) and par[node].func is node
            if node.id in scope and not callee:
                new = _typo(node.id, scope | func_names, rng)
                if new:
                    def f(n, t, new=new):
                        n.id = new
                    add("name_typo", node, f, f"{node.id} -> {new}")
                for other in sorted(scope - {node.id} - func_names):
                    def f(n, t, other=other):
                        n.id = other
                    add("wrong_variable", node, f, f"{node.id} -> {other}")
        # ---- function-level: missing_return, missing_edge_case, missing_base_case
        if isinstance(node, ast.FunctionDef):
            params = {a.arg for a in node.args.args}
            last = node.body[-1] if node.body else None
            if isinstance(last, ast.Return) and last.value is not None:
                def f(n, t):
                    n.body.pop()
                    if not n.body:
                        n.body.append(ast.Pass())
                add("missing_return", node, f, "final return deleted")

                def f(n, t):
                    r = n.body[-1]
                    n.body[-1] = ast.Expr(ast.Call(ast.Name("print", ast.Load()), [r.value], []))
                add("missing_return", node, f, "return -> print")
                # lst.sort() ; return lst   ->   return lst.sort()
                prev = node.body[-2] if len(node.body) >= 2 else None
                if isinstance(prev, ast.Expr) and isinstance(prev.value, ast.Call) and \
                        isinstance(prev.value.func, ast.Attribute) and \
                        prev.value.func.attr in ("sort", "reverse") and \
                        isinstance(last.value, ast.Name) and \
                        isinstance(prev.value.func.value, ast.Name) and \
                        prev.value.func.value.id == last.value.id:
                    def f(n, t):
                        call = n.body[-2].value
                        n.body[-2:] = [ast.Return(call)]
                    add("missing_return", node, f, "return lst.sort()")
            # read the first/last item before checking the list is empty (biggest = lst[0]),
            # the commonest way real students crash on an empty input
            for pidx, a in enumerate(node.args.args):
                has_empty = any((t[2] if len(t) > 2 else problem["function"]) == node.name
                                and pidx < len(t[0]) and hasattr(t[0][pidx], "__len__")
                                and len(t[0][pidx]) == 0 for t in problem["tests"])
                if not has_empty:
                    continue
                for where, name in (("0", "first"), ("-1", "last")):
                    stmt = f"{name} = {a.arg}[{where}]"

                    def f(n, t, stmt=stmt):
                        if stmt.split(" =")[0] in _scope_names(n):
                            return False
                        n.body.insert(0, ast.parse(stmt).body[0])
                    add("missing_edge_case", node, f, f"reads {a.arg}[{where}] first",
                        node.lineno + 1)
            recursive = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and
                            c.func.id == node.name for c in ast.walk(node))
            for st in node.body[:4]:
                if not isinstance(st, ast.If) or not st.body or \
                        not isinstance(st.body[-1], ast.Return):
                    continue
                calls_self = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and
                                 c.func.id == node.name for c in ast.walk(st))
                if _is_emptiness_test(st.test, params):
                    si = node.body.index(st)

                    def f(n, t, si=si):
                        guard = n.body[si]
                        n.body[si:si + 1] = guard.orelse      # keep any else-part
                        if not n.body:
                            n.body.append(ast.Pass())
                    add("missing_edge_case", node, f, "empty-input check deleted", st.lineno)
                elif recursive and not calls_self:
                    si = node.body.index(st)

                    def f(n, t, si=si):
                        guard = n.body[si]
                        n.body[si:si + 1] = guard.orelse
                    add("missing_base_case", node, f, "base case deleted", st.lineno)
        # ---- early_return: the return after a loop moves into the loop
        if isinstance(node, (ast.For, ast.While)) and in_func:
            holder = par[node]
            for field in ("body", "orelse"):
                stmts = getattr(holder, field, None)
                if not isinstance(stmts, list) or node not in stmts:
                    continue
                i = stmts.index(node)
                if i + 1 >= len(stmts) or not isinstance(stmts[i + 1], ast.Return):
                    continue
                last_in_loop = node.body[-1]
                if isinstance(last_in_loop, ast.If) and not last_in_loop.orelse:
                    def f(n, t, field=field):
                        h = _parents(t)[n]
                        lst = getattr(h, field)
                        r = lst.pop(lst.index(n) + 1)
                        n.body[-1].orelse = [r]
                    add("early_return", node, f, "return after loop -> else: inside loop")

                def f(n, t, field=field):
                    h = _parents(t)[n]
                    lst = getattr(h, field)
                    r = lst.pop(lst.index(n) + 1)
                    n.body.append(r)
                add("early_return", node, f, "return after loop -> last line of loop")
    return out


# ----------------------------------------------------------------------------- filtering
def run_details(code, problem):
    passed, details = evaluate(code, problem, detail=True)
    return passed, details


def dynamic_features(problem, details):
    """What the program DOES on the tests (P2's dynamic features)."""
    tests = problem["tests"]
    fails = [i for i, d in enumerate(details) if not d[0]]
    errs = Counter(d[2] for d in details if not d[0] and d[2])
    empty_fail = [i for i in fails if _is_empty_test(tests[i])]
    return {
        "num_passed": len(details) - len(fails), "num_total": len(details),
        "crashed": bool(errs), "errors": dict(errs),
        "timeout": "Timeout" in errs,
        "returns_none": sum(1 for i in fails if details[i][1] == "None" and not details[i][2]),
        "fails_on_empty": bool(empty_fail),
        "only_empty_fails": bool(fails) and len(empty_fail) == len(fails),
        "fails_non_empty": len(fails) > len(empty_fail),
        "failed_tests": fails,
    }


def acceptable(label, dyn):
    """Does this mutant really show the bug its label claims (the agreed rules)?"""
    if dyn["num_passed"] == dyn["num_total"]:
        return False                                  # equivalent mutant: label would be a lie
    if label == "missing_edge_case":
        return dyn["fails_on_empty"]
    if label == "early_return":
        return dyn["fails_non_empty"]
    if label == "name_typo":
        return "NameError" in dyn["errors"] or "UnboundLocalError" in dyn["errors"]
    return True


def mutate_program(code, problem, problem_id, base_id, seed, double_rate):
    rng = random.Random(f"{seed}:{base_id}")
    # Reformat first (drops comments/blank lines), so the line numbers we record match the
    # saved mutant code exactly. Mutants are written in this same format.
    code = ast.unparse(ast.parse(code))
    base_passed, _ = run_details(code, problem)
    if base_passed != len(problem["tests"]):
        return [], {"skipped_base": 1}
    stats = Counter()
    rows = []
    sites = injections(code, problem, rng)
    by_type = {}
    for s in sites:
        by_type.setdefault(s["label"], []).append(s)
    kept = {}
    for label in BUG_TYPES:
        options = by_type.get(label, [])
        rng.shuffle(options)
        for s in options[:SITES_TRIED]:
            new = s["make"]()
            if new is None:
                continue
            _, details = run_details(new, problem)
            dyn = dynamic_features(problem, details)
            if not acceptable(label, dyn):
                stats[f"rejected:{label}"] += 1
                continue
            kept[label] = (s, new, dyn)
            rows.append({"problem_id": problem_id, "base_id": base_id, "labels": [label],
                         "lines": [s["line"]], "edits": [s["edit"]], "code": new,
                         "dynamic": dyn})
            break
    # two bugs at once: put a second, different bug into a kept mutant
    if kept and rng.random() < double_rate:
        firsts = [lab for lab in sorted(kept) if lab != "name_typo"] or sorted(kept)
        first = rng.choice(firsts)
        s1, code1, dyn1 = kept[first]
        seconds = [lab for lab in BUG_TYPES if lab != first]
        rng.shuffle(seconds)
        for second in seconds:
            opts = [s for s in injections(code1, problem, rng) if s["label"] == second]
            rng.shuffle(opts)
            done = False
            for s2 in opts[:SITES_TRIED]:
                new = s2["make"]()
                if new is None:
                    continue
                _, details = run_details(new, problem)
                dyn = dynamic_features(problem, details)
                # the second bug must change what the program does, or it is invisible
                if not acceptable(second, dyn) or dyn["failed_tests"] == dyn1["failed_tests"] \
                        and dyn["errors"] == dyn1["errors"]:
                    continue
                rows.append({"problem_id": problem_id, "base_id": base_id,
                             "labels": [first, second], "lines": [s1["line"], s2["line"]],
                             "edits": [s1["edit"], s2["edit"]], "code": new, "dynamic": dyn})
                done = True
                break
            if done:
                break
    return rows, stats


# ----------------------------------------------------------------------------- driver
_JOBS = {}


def _child(key, conn):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    sys.setrecursionlimit(400)
    code, problem, problem_id, base_id, seed, double = _JOBS[key]
    try:
        out = mutate_program(code, problem, problem_id, base_id, seed, double)
    except BaseException as exc:  # noqa: BLE001
        out = ([], {"error:" + type(exc).__name__: 1})
    conn.send(out)
    conn.close()


def run_all(keys, workers):
    ctx = mp.get_context("fork")
    pending, active, results, done = list(keys), {}, {}, 0
    while pending or active:
        while pending and len(active) < workers:
            key = pending.pop(0)
            r, w = ctx.Pipe(False)
            p = ctx.Process(target=_child, args=(key, w))
            p.start()
            w.close()
            active[p] = (key, r, time.time())
        for p, (key, r, t0) in list(active.items()):
            if r.poll():
                try:
                    results[key] = r.recv()
                except EOFError:
                    results[key] = ([], {"crash": 1})
            elif not p.is_alive():
                results[key] = ([], {"crash": 1})
            elif time.time() - t0 > PROGRAM_SECONDS:
                p.kill()
                results[key] = ([], {"too_slow": 1})
            else:
                continue
            p.join(1)
            del active[p]
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(keys)} programs", flush=True)
        time.sleep(0.002)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    ap.add_argument("--no-toy", action="store_true", help="skip the 5 toy problems")
    ap.add_argument("--limit", type=int, default=0, help="correct programs per NUS question")
    ap.add_argument("--double", type=float, default=0.25, help="share of programs that also "
                    "get a two-bug mutant")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=os.path.join("data", "mutants.jsonl"))
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    args = ap.parse_args()

    bases = []          # (problem_id, base_id, code, problem)
    if not args.no_toy:
        for pid in PROBLEM_IDS:
            p = get_problem(pid)
            for i, sol in enumerate(p["solutions"]):
                bases.append((pid, f"{pid}/solution_{i + 1}", sol, p))
    for q in args.questions:
        p = nus.load_question(nus_data_dir(), q)
        bases.append((p["id"], f"{p['id']}/reference", p["solutions"][0], p))
        for path in nus.list_programs(p, "correct", limit=args.limit or None):
            bases.append((p["id"], f"{p['id']}/{os.path.basename(path)}",
                          nus.program_code(path), p))

    # drop correct programs that are near-copies of each other (same code after renaming)
    seen, unique = set(), []
    for b in bases:
        key = (b[0], normalize(b[2]))
        if key not in seen:
            seen.add(key)
            unique.append(b)
    print(f"{len(bases)} correct programs, {len(unique)} after removing near-copies", flush=True)

    keys = []
    for i, (pid, bid, code, p) in enumerate(unique):
        _JOBS[i] = (code, p, pid, bid, args.seed, args.double)
        keys.append(i)
    t0 = time.time()
    results = run_all(keys, args.workers)

    rows, stats, seen_mut = [], Counter(), set()
    for k in keys:
        r, s = results[k]
        stats.update(s)
        for row in r:
            key = (row["problem_id"], normalize(row["code"]))
            if key in seen_mut:
                stats["duplicate_mutant"] += 1
                continue
            seen_mut.add(key)
            row["id"] = f"m{len(rows):06d}"
            rows.append(row)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({k: row[k] for k in FIELDS}) + "\n")

    print(f"\n{len(rows)} mutants from {len(unique)} programs in {time.time() - t0:.0f}s "
          f"-> {args.out}")
    problems = sorted({r["problem_id"] for r in rows})
    print("\nmutants containing each bug type, per problem:")
    print("bug type".ljust(18) + "".join(p[-8:].rjust(10) for p in problems) + "total".rjust(8))
    for lab in BUG_TYPES:
        cnt = [sum(1 for r in rows if r["problem_id"] == p and lab in r["labels"])
               for p in problems]
        print(lab.ljust(18) + "".join(str(c).rjust(10) for c in cnt) + str(sum(cnt)).rjust(8))
    print("\none bug / two bugs:", dict(Counter(len(r["labels"]) for r in rows)))
    print("rejected or skipped:", dict(stats.most_common()))


if __name__ == "__main__":
    main()
