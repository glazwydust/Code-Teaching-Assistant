"""P2: bug-type classifier. Given a wrong program, guess WHICH kind of bug it has.

    from classifier import predict_bugs
    predict_bugs(code, "nus_q1")
    -> {"bugs": [("missing_edge_case", 0.91), ("off_by_one", 0.22), ...],   # most likely first
        "not_sure": False,      # True = the model is not confident, show a general hint instead
        "reason": "ok"}         # or "passes_tests" / "syntax_error"

How it decides. Two kinds of evidence go into one logistic-regression model per bug type:
  1. What the code LOOKS like: its words and symbols with variable names replaced by v0, v1, ...
     (so "count" vs "total" does not matter), counted as 1-3 word phrases (TF-IDF), plus a few
     structure checks (a return inside a loop? a list read with [0] before any empty check? ...).
  2. What the code DOES on the tests (from P1's run_code): did it crash and with which error,
     return None, fail only on the empty input, miss the answer by exactly 1, ...

The trained model is a plain JSON file (models/bug_classifier.json), so this file needs nothing
beyond Python's standard library: no scikit-learn, no pickle, runs the same on Windows/Mac/Linux.
To retrain: scripts/train.py (needs Linux/Mac or WSL; see that file).
"""
import ast
import io
import json
import keyword
import math
import os
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "models", "bug_classifier.json")

ERROR_TYPES = ["NameError", "UnboundLocalError", "IndexError", "TypeError", "AttributeError",
               "KeyError", "ValueError", "RecursionError", "ZeroDivisionError", "Timeout",
               "MissingFunction", "SyntaxError"]


# ----------------------------------------------------------------------------- code as text
def core_code(code):
    """Only the functions (and imports) of a program. Students often paste example lines under
    their function (my_list = [...]; print(f(my_list))); training programs have none, so those
    lines are left out of what the model READS. The tests still run the whole program."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code
    keep = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
    tree.body = [n for n in tree.body if isinstance(n, keep)]
    return ast.unparse(tree)


def normalize_tokens(code):
    """Tokens of the program, with every name the student chose renamed v0, v1, ... in order of
    first use. Built-in names (len, range, print, ...) and keywords are kept as they are."""
    import builtins
    keep = set(dir(builtins)) | set(keyword.kwlist)
    code = core_code(code)                          # same layout for everyone, comments dropped
    out, names = [], {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.NAME:
                out.append(tok.string if tok.string in keep else
                           names.setdefault(tok.string, f"v{len(names)}"))
            elif tok.type == tokenize.NUMBER:
                out.append(tok.string if tok.string in ("0", "1", "2") else "NUM")
            elif tok.type == tokenize.STRING:
                out.append("STR")
            elif tok.type == tokenize.OP:
                out.append(tok.string)
            elif tok.type == tokenize.INDENT:
                out.append("INDENT")
            elif tok.type == tokenize.DEDENT:
                out.append("DEDENT")
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def ngrams(code, n_max=3):
    """The 'words' the text model counts: every run of 1-3 tokens."""
    toks = normalize_tokens(code)
    grams = []
    for n in range(1, n_max + 1):
        for i in range(len(toks) - n + 1):
            grams.append(" ".join(toks[i:i + n]))
    return grams


# ----------------------------------------------------------------------------- code structure
def _is_emptiness_test(test, params):
    for n in ast.walk(test):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not) and \
                isinstance(n.operand, ast.Name) and n.operand.id in params:
            return True
        if isinstance(n, ast.Compare):
            parts = [n.left] + n.comparators
            has_param = any(isinstance(p, ast.Name) and p.id in params for p in parts) or \
                any(isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and
                    p.func.id == "len" for p in parts)
            has_empty = any((isinstance(p, (ast.List, ast.Tuple)) and not p.elts) or
                            (isinstance(p, ast.Constant) and p.value == 0) for p in parts)
            if has_param and has_empty:
                return True
    return False


def structure_features(code):
    f = {k: 0.0 for k in ["parse_error", "return_in_loop", "func_ends_without_return",
                          "has_print_in_func", "has_empty_guard", "reads_item_before_guard",
                          "recursive", "has_while", "range_len_minus_1", "init_inside_loop",
                          "returns_inplace_call", "assigns_inplace_call", "n_compares",
                          "n_loops", "n_returns", "undefined_names"]}
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):
        f["parse_error"] = 1.0
        return f
    code = core_code(code)
    tree = ast.parse(code)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    inplace = {"sort", "reverse", "append", "extend", "insert", "remove"}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        params = {a.arg for a in fn.args.args}
        if fn.body and not isinstance(fn.body[-1], (ast.Return, ast.Raise)):
            f["func_ends_without_return"] = 1.0
        guarded = False
        for st in fn.body:
            if isinstance(st, ast.If) and _is_emptiness_test(st.test, params):
                f["has_empty_guard"] = 1.0
                guarded = True
            if not guarded:
                # an item of a parameter read ([0] / [-1]) before any empty check
                for n in ast.walk(st):
                    if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and \
                            n.value.id in params and isinstance(n.slice, (ast.Constant, ast.UnaryOp)):
                        f["reads_item_before_guard"] = 1.0
            if isinstance(st, (ast.For, ast.While)):
                break
        if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == fn.name
               for c in ast.walk(fn)):
            f["recursive"] = 1.0
    for n in ast.walk(tree):
        if isinstance(n, (ast.For, ast.While)):
            f["n_loops"] += 1
            f["has_while"] = max(f["has_while"], float(isinstance(n, ast.While)))
            for st in n.body:
                if isinstance(st, ast.Return):
                    f["return_in_loop"] = 1.0
                if isinstance(st, ast.If) and any(isinstance(s, ast.Return) for s in st.orelse):
                    f["return_in_loop"] = 1.0
                if isinstance(st, ast.Assign) and isinstance(st.value, (ast.Constant, ast.List)):
                    f["init_inside_loop"] = 1.0
        if isinstance(n, ast.Return):
            f["n_returns"] += 1
            v = n.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in inplace:
                f["returns_inplace_call"] = 1.0
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and \
                isinstance(n.value.func, ast.Attribute) and n.value.func.attr in inplace:
            f["assigns_inplace_call"] = 1.0
        if isinstance(n, ast.Compare):
            f["n_compares"] += len(n.ops)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print":
            p = parents.get(n)
            while p is not None and not isinstance(p, ast.FunctionDef):
                p = parents.get(p)
            if p is not None:
                f["has_print_in_func"] = 1.0
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "range" \
                and n.args and isinstance(n.args[-1], ast.BinOp) and \
                isinstance(n.args[-1].op, ast.Sub):
            f["range_len_minus_1"] = 1.0
    try:
        from runner import find_undefined_names
        f["undefined_names"] = float(min(3, len(find_undefined_names(code))))
    except Exception:  # noqa: BLE001
        pass
    for k in ("n_compares", "n_loops", "n_returns"):
        f[k] = math.log1p(f[k])
    return f


# ----------------------------------------------------------------------------- test behaviour
def _is_empty(value):
    return hasattr(value, "__len__") and not isinstance(value, str) and len(value) == 0


def outcomes_from_runner(result, problem):
    """Turn P1's run_code() result into one record per test:
    {passed, actual, expected, error (type name or None), empty (an input is an empty list)}"""
    out = []
    tests = result.get("tests") or []
    if result.get("status") == "syntax_error" or not tests:
        for t in problem["tests"]:
            out.append({"passed": False, "actual": None, "expected": t[1],
                        "error": "SyntaxError", "empty": any(_is_empty(a) for a in t[0])})
        return out
    for t in tests:
        err = t.get("error")
        if err:
            if err.startswith("Timed out") or err.startswith("Skipped"):
                err = "Timeout"
            elif err.startswith("Function '") and err.endswith("is not defined"):
                err = "MissingFunction"
            else:
                err = err.split(":")[0].strip()
        out.append({"passed": bool(t.get("passed")), "actual": t.get("actual"),
                    "expected": t.get("expected"), "error": err,
                    "empty": any(_is_empty(a) for a in t.get("input", []))})
    return out


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def behaviour_features(outcomes):
    n = max(1, len(outcomes))
    fails = [o for o in outcomes if not o["passed"]]
    nf = max(1, len(fails))
    errs = [o["error"] for o in fails if o["error"]]
    f = {"frac_passed": (len(outcomes) - len(fails)) / n,
         "crashed": float(bool(errs)),
         "frac_fail_crash": len(errs) / nf}
    for e in ERROR_TYPES:
        f["err_" + e] = float(e in errs)
    f["err_other"] = float(any(e not in ERROR_TYPES for e in errs))
    quiet = [o for o in fails if not o["error"]]
    f["frac_fail_returns_none"] = sum(o["actual"] is None for o in quiet) / nf
    empty_fail = [o for o in fails if o["empty"]]
    f["fails_on_empty"] = float(bool(empty_fail))
    f["only_empty_fails"] = float(bool(fails) and len(empty_fail) == len(fails))
    f["fails_non_empty"] = float(len(fails) > len(empty_fail))
    f["frac_fail_off_by_1"] = sum(1 for o in quiet if _num(o["actual"]) and _num(o["expected"])
                                  and abs(o["actual"] - o["expected"]) == 1) / nf
    f["frac_fail_len_off_by_1"] = sum(
        1 for o in quiet if isinstance(o["actual"], (list, tuple)) and
        isinstance(o["expected"], (list, tuple)) and
        abs(len(o["actual"]) - len(o["expected"])) == 1) / nf
    f["frac_fail_bool_flipped"] = sum(
        1 for o in quiet if isinstance(o["actual"], bool) and isinstance(o["expected"], bool)
        and o["actual"] != o["expected"]) / nf
    f["frac_fail_zero"] = sum(1 for o in quiet if o["actual"] in (0, [], ())
                              and o["actual"] is not False) / nf
    return f


def dense_features(code, outcomes):
    d = structure_features(code)
    d.update(behaviour_features(outcomes))
    return d


# ----------------------------------------------------------------------------- the model
_MODEL = None


def load_model(path=MODEL_PATH):
    global _MODEL
    if _MODEL is None or path != MODEL_PATH:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        m["vocab_index"] = {g: i for i, g in enumerate(m["vocab"])}
        if path != MODEL_PATH:
            return m
        _MODEL = m
    return _MODEL


def probabilities(code, outcomes, model=None):
    """Probability of each bug type (dict), from the JSON model, standard library only."""
    m = model or load_model()
    counts = {}
    for g in ngrams(code):
        i = m["vocab_index"].get(g)
        if i is not None:
            counts[i] = counts.get(i, 0) + 1
    tfidf = {i: (1.0 + math.log(c)) * m["idf"][i] for i, c in counts.items()}
    norm = math.sqrt(sum(v * v for v in tfidf.values())) or 1.0
    tfidf = {i: v / norm for i, v in tfidf.items()}
    dense = dense_features(code, outcomes)
    dvec = [(dense.get(name, 0.0) - mu) / sd
            for name, mu, sd in zip(m["dense_names"], m["dense_mean"], m["dense_scale"])]
    n_text = len(m["vocab"])
    probs = {}
    for k, label in enumerate(m["classes"]):
        w = m["coef"][k]
        z = m["intercept"][k]
        z += sum(w[i] * v for i, v in tfidf.items())
        z += sum(w[n_text + j] * v for j, v in enumerate(dvec))
        probs[label] = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))
    return probs


CONF_DENSE = ["frac_passed", "crashed", "only_empty_fails", "fails_non_empty",
              "frac_fail_returns_none", "err_NameError", "err_Timeout", "err_TypeError",
              "err_IndexError"]
CONF_NAMES = ["top", "margin", "sum", "entropy"] + CONF_DENSE


def confidence_features(prob_list, dense):
    """Evidence for 'is the top guess trustworthy?': how far ahead the top bug type is, and how
    the program behaved. prob_list is in the model's class order."""
    s = sorted(prob_list)
    total = sum(prob_list) or 1e-9
    shares = [max(1e-9, p / total) for p in prob_list]
    ent = -sum(q * math.log(q) for q in shares)
    return [s[-1], s[-1] - s[-2], sum(prob_list), ent] + [dense.get(k, 0.0) for k in CONF_DENSE]


def confidence(probs, dense, model=None):
    """Probability that the top guess is right (a small second model trained on real programs)."""
    m = model or load_model()
    c = m["confidence_model"]
    x = confidence_features([probs[b] for b in m["classes"]], dense)
    z = c["intercept"] + sum(w * (v - mu) / sd for w, v, mu, sd in
                             zip(c["coef"], x, c["mean"], c["scale"]))
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))


def predict_bugs(code, problem_id, run_result=None, model=None):
    """Main entry point for the app (P3/P4). problem_id is an id like "nus_q1" or "sum_list".
    Pass run_result if you already called run_code(code, problem_id), to avoid running twice."""
    from problems import get_problem
    from runner import run_code
    m = model or load_model()
    problem = problem_id if isinstance(problem_id, dict) else get_problem(problem_id)
    if run_result is None:
        run_result = run_code(code, problem)
    if run_result.get("status") == "passed":
        return {"bugs": [], "not_sure": False, "reason": "passes_tests"}
    if run_result.get("status") == "syntax_error":
        return {"bugs": [], "not_sure": True, "reason": "syntax_error"}
    outcomes = outcomes_from_runner(run_result, problem)
    probs = probabilities(code, outcomes, m)
    conf = confidence(probs, dense_features(code, outcomes), m)
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    return {"bugs": [(lab, round(p, 3)) for lab, p in ranked],
            "confidence": round(conf, 3),        # chance the FIRST bug type is right
            "not_sure": conf < m["not_sure_threshold"], "reason": "ok"}


if __name__ == "__main__":
    demo = "def search(x, seq):\n    first = seq[0]\n    for i, e in enumerate(seq):\n" \
           "        if x <= e:\n            return i\n    return len(seq)\n"
    print(predict_bugs(demo, "nus_q1"))
