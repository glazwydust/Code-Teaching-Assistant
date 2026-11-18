"""P2: train the bug-type classifier and test it honestly.

Inputs : data/mutants.jsonl   (training bugs made by scripts/mutate.py)
         auto_labels.csv      (real wrong programs, labelled by scripts/auto_label.py)
         labels_real_50.csv   (real wrong programs, labelled by hand - the final test)
Outputs: models/bug_classifier.json   the trained model (used by classifier.py / the app)
         reports/p2_results.md         every number for the report, in tables
         reports/p2_*.png              figures
         reports/p2_metrics.json       the same numbers, machine-readable

Run from the codeEd folder (Linux / Mac / WSL - it runs student code with Unix timers):

    python3 scripts/train.py

What it does, in order:
  1. Runs every program once on its tests and records what happened (cached in data/).
  2. Picks the model's strength setting (C) using 5-fold cross-validation split BY base program,
     so the mutants of one correct program are never on both sides of a split.
  3. Generated-bug scores (cross-validation), then the "new problem" test: train on 4 of the 5
     NUS problems, test on the 5th.
  4. Real-bug scores: auto-labelled real programs, and Krishna's hand-labelled 50 (never used
     for any choice, so it is a clean final test).
  5. Chooses the "not sure" cut-off on the auto-labelled real programs (NOT on the 50).
  6. Compares with two baselines: always guess the commonest bug, and the app's old text rules.
"""
import copy
import csv
import hashlib
import io
import json
import math
import multiprocessing as mp
import os
import signal
import sys
import time
import warnings
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import GroupKFold  # noqa: E402
from sklearn.multiclass import OneVsRestClassifier  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

import classifier as C  # noqa: E402
from problems import get_problem  # noqa: E402

BUGS = ["missing_edge_case", "wrong_operator", "off_by_one", "missing_return", "name_typo",
        "wrong_variable", "wrong_init", "early_return", "missing_base_case"]
DATA = "data"
REPORTS = "reports"
SEED = 0
EXTRA_TAU = 0.5      # a second bug type is also reported when its probability is >= this (tuned)


# ----------------------------------------------------------------------------- run programs
class _Timeout(BaseException):
    pass


def _alarm(signum, frame):
    raise _Timeout()


class _Sink(io.StringIO):
    def write(self, s):
        return len(s)


def _jsonable(v):
    """What the actual value looks like after P1's runner sends it back (JSON round trip)."""
    try:
        return json.loads(json.dumps(v))
    except (TypeError, ValueError):
        return repr(v)


def fast_outcomes(code, problem):
    """Same records as classifier.outcomes_from_runner(run_code(...)), but in one process
    (thousands of programs in minutes instead of hours). Checked against the runner below."""
    tests = problem["tests"]
    empty = [any(C._is_empty(a) for a in t[0]) for t in tests]
    try:
        compiled = compile(code, "<student>", "exec")
    except (SyntaxError, ValueError):
        return [{"passed": False, "actual": None, "expected": t[1], "error": "SyntaxError",
                 "empty": e} for t, e in zip(tests, empty)]
    sys.stdout, sys.stdin = _Sink(), io.StringIO("")
    signal.signal(signal.SIGALRM, _alarm)
    strict = problem.get("strict_types", True)
    module_err = None
    ns = {"__name__": "student_module"}
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        if problem.get("prelude"):
            exec(problem["prelude"], ns)
        exec(compiled, ns)
    except _Timeout:
        module_err = "Timeout"
    except BaseException as exc:  # noqa: BLE001
        module_err = type(exc).__name__
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    out = []
    timeouts = 0
    for t, e in zip(tests, empty):
        rec = {"passed": False, "actual": None, "expected": t[1], "error": None, "empty": e}
        if timeouts >= 2:      # P1's runner skips the remaining tests after ~2 time-outs
            rec["error"] = "Timeout"
            out.append(rec)
            continue
        if module_err:
            rec["error"] = module_err
            out.append(rec)
            continue
        fname = t[2] if len(t) > 2 else problem["function"]
        func = ns.get(fname)
        if not callable(func):
            rec["error"] = "MissingFunction"
            out.append(rec)
            continue
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        try:
            actual = func(*copy.deepcopy(t[0]))
            signal.setitimer(signal.ITIMER_REAL, 0)
            try:
                rec["passed"] = bool(actual == t[1]) and (not strict or type(actual) is type(t[1]))
            except Exception:  # noqa: BLE001
                rec["passed"] = False
            rec["actual"] = _jsonable(actual)
        except _Timeout:
            rec["error"] = "Timeout"
            timeouts += 1
        except BaseException as exc:  # noqa: BLE001
            rec["error"] = type(exc).__name__
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        out.append(rec)
    sys.stdout, sys.stdin = sys.__stdout__, sys.__stdin__
    return out


_JOBS = []


def _child(i, conn):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 << 20, 768 << 20))
    sys.setrecursionlimit(400)
    code, pid = _JOBS[i]
    try:
        res = fast_outcomes(code, get_problem(pid))
    except BaseException:  # noqa: BLE001
        res = None
    conn.send(res)
    conn.close()


def outcomes_for(items, cache_path, workers=None):
    """items: list of (code, problem_id). Returns list of outcome lists (cached on disk)."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    keys = [hashlib.sha1((pid + "\0" + code).encode()).hexdigest() for code, pid in items]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    if todo:
        print(f"  running {len(todo)} programs on their tests...", flush=True)
        _JOBS.clear()
        _JOBS.extend(items)
        ctx = mp.get_context("fork")
        workers = workers or max(1, os.cpu_count() or 1)
        pending, active = list(todo), {}
        while pending or active:
            while pending and len(active) < workers:
                i = pending.pop(0)
                r, w = ctx.Pipe(False)
                p = ctx.Process(target=_child, args=(i, w))
                p.start()
                w.close()
                active[p] = (i, r, time.time())
            for p, (i, r, t0) in list(active.items()):
                res = "wait"
                if r.poll():
                    try:
                        res = r.recv()
                    except EOFError:
                        res = None
                elif not p.is_alive() or time.time() - t0 > 30:
                    res = None
                if res == "wait":
                    continue
                if res is None:   # crashed or hung as a whole: treat every test as a timeout
                    prob = get_problem(items[i][1])
                    res = [{"passed": False, "actual": None, "expected": t[1],
                            "error": "Timeout", "empty": any(C._is_empty(a) for a in t[0])}
                           for t in prob["tests"]]
                cache[keys[i]] = res
                p.kill()
                p.join(1)
                del active[p]
            time.sleep(0.002)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    return [cache[k] for k in keys]


# ----------------------------------------------------------------------------- data
def load_mutants():
    rows = [json.loads(line) for line in open(os.path.join(DATA, "mutants.jsonl"), encoding="utf-8")]
    return rows


def real_code(q, file):
    path = os.path.join("refactory_data", "data", f"question_{q}", "code", "wrong", file)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def load_real():
    auto = []
    with open("auto_labels.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["primary"] == "passes_tests":
                continue
            labs = [x for x in r["labels"].split(";") if x]
            auto.append({"problem_id": f"nus_q{r['question']}", "file": r["file"],
                         "code": real_code(r["question"], r["file"]), "labels": labs,
                         "confidence": r["confidence"]})
    human = []
    with open("labels_real_50.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            human.append({"problem_id": f"nus_q{r['question']}", "file": r["file"],
                          "code": real_code(r["question"], r["file"]),
                          "labels": [x.strip() for x in r["label"].split(";") if x.strip()]})
    return auto, human


def dense_matrix(codes, outcomes):
    dicts = [C.dense_features(c, o) for c, o in zip(codes, outcomes)]
    names = sorted(dicts[0])
    return np.array([[d[n] for n in names] for d in dicts], dtype=float), names


def multi_hot(label_lists):
    return np.array([[int(b in labs) for b in BUGS] for labs in label_lists])


# ----------------------------------------------------------------------------- model
class Model:
    """TF-IDF over normalized code + scaled behaviour/structure features -> one logistic
    regression per bug type."""

    def __init__(self, c=1.0, use_text=True, use_dense=True):
        self.c, self.use_text, self.use_dense = c, use_text, use_dense

    def _x(self, grams, dense, fit):
        parts = []
        if self.use_text:
            parts.append(self.vec.fit_transform(grams) if fit else self.vec.transform(grams))
        if self.use_dense:
            parts.append(sparse.csr_matrix(self.scaler.fit_transform(dense) if fit
                                           else self.scaler.transform(dense)))
        return sparse.hstack(parts).tocsr()

    def fit(self, grams, dense, y):
        self.vec = TfidfVectorizer(analyzer=lambda g: g, sublinear_tf=True, min_df=2,
                                   max_features=4000)
        self.scaler = StandardScaler()
        x = self._x(grams, dense, True)
        self.keep = [k for k in range(y.shape[1]) if 0 < y[:, k].sum() < len(y)]
        self.clf = OneVsRestClassifier(LogisticRegression(C=self.c, class_weight="balanced",
                                                          solver="liblinear", max_iter=3000))
        self.clf.fit(x, y[:, self.keep])
        return self

    def proba(self, grams, dense):
        p = np.zeros((len(grams), len(BUGS)))
        p[:, self.keep] = self.clf.predict_proba(self._x(grams, dense, False))
        return p

    def export(self, dense_names, threshold, info):
        assert self.use_text and self.use_dense and len(self.keep) == len(BUGS)
        vocab = [None] * len(self.vec.vocabulary_)
        for g, i in self.vec.vocabulary_.items():
            vocab[i] = g
        scale = [float(s) if s > 0 else 1.0 for s in self.scaler.scale_]
        return {"version": 1, "classes": BUGS, "vocab": vocab,
                "idf": [round(float(v), 6) for v in self.vec.idf_],
                "dense_names": dense_names,
                "dense_mean": [round(float(v), 6) for v in self.scaler.mean_],
                "dense_scale": [round(v, 6) for v in scale],
                "coef": [[round(float(w), 6) for w in est.coef_.ravel()]
                         for est in self.clf.estimators_],
                "intercept": [round(float(est.intercept_[0]), 6) for est in self.clf.estimators_],
                "not_sure_threshold": threshold, "trained_on": info}


# ----------------------------------------------------------------------------- metrics
def predicted_sets(p, tau=None):
    tau = EXTRA_TAU if tau is None else tau
    return [{BUGS[int(np.argmax(r))]} | {BUGS[k] for k in range(len(BUGS)) if r[k] >= tau}
            for r in p]


def scores(p, label_lists, tau=None):
    """top-1: the most likely bug is one of the true ones. Per-type precision/recall/F1 use the
    predicted set = top bug + any other bug with probability >= 0.5."""
    preds = predicted_sets(p, tau)
    top = [BUGS[int(np.argmax(r))] for r in p]
    n = len(label_lists)
    res = {"n": n, "top1": sum(t in labs for t, labs in zip(top, label_lists)) / max(1, n),
           "exact": sum(pr == set(labs) for pr, labs in zip(preds, label_lists)) / max(1, n),
           "per_type": {}}
    f1s = []
    for b in BUGS:
        tp = sum(b in pr and b in labs for pr, labs in zip(preds, label_lists))
        fp = sum(b in pr and b not in labs for pr, labs in zip(preds, label_lists))
        fn = sum(b not in pr and b in labs for pr, labs in zip(preds, label_lists))
        support = tp + fn
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / support if support else float("nan")
        f1 = 2 * prec * rec / (prec + rec) if support and prec + rec else 0.0
        res["per_type"][b] = {"precision": prec, "recall": rec, "f1": f1, "support": support}
        if support:
            f1s.append(f1)
    res["macro_f1"] = float(np.mean(f1s)) if f1s else 0.0
    return res


def decisions(p, label_lists, conf, t):
    """With 'not sure' when the confidence model says < t: answered/correct counts. A real
    program labelled 'other' is handled correctly only if the model says 'not sure'."""
    out = {"answered": 0, "answered_right": 0, "abstained": 0, "other_abstained": 0,
           "other": 0, "n": len(label_lists)}
    for r, labs, c in zip(p, label_lists, conf):
        top = BUGS[int(np.argmax(r))]
        is_other = set(labs) <= {"other"}
        out["other"] += is_other
        if c < t:
            out["abstained"] += 1
            out["other_abstained"] += is_other
        else:
            out["answered"] += 1
            out["answered_right"] += top in labs
    out["coverage"] = out["answered"] / max(1, out["n"])
    out["accuracy_answered"] = out["answered_right"] / max(1, out["answered"])
    out["decision_accuracy"] = (out["answered_right"] + out["other_abstained"]) / max(1, out["n"])
    return out


def conf_matrix_x(p, dense, dense_names):
    """Confidence-model inputs for many programs (same as classifier.confidence_features)."""
    rows = []
    for r, d in zip(p, dense):
        rows.append(C.confidence_features(list(r), dict(zip(dense_names, d))))
    return np.array(rows)


def right_or_not(p, label_lists):
    return np.array([int(BUGS[int(np.argmax(r))] in labs) for r, labs in zip(p, label_lists)])


def app_rules(code, problem_id):
    """The bug guess app.py made before the model existed (copied from app.py)."""
    detected = "off_by_one"
    for line in code.splitlines():
        if problem_id == "is_palindrome" and ("reverse = s" in line or "rev = s" in line):
            return "wrong_init"
        if "range" in line:
            return "off_by_one"
        if "=" in line and "==" not in line and "!=" not in line and \
                any(op in line for op in "+-*/"):
            return "wrong_operator"
    return detected


def fixed_guess_p(label):
    p = np.zeros(len(BUGS))
    p[BUGS.index(label)] = 1.0
    return p


# ----------------------------------------------------------------------------- figures
INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, ORANGE = "#2a78d6", "#eb6834"


def _style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK2)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def figures(curve, chosen_t, synth, real, conf_mat, conf_labels):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(REPORTS, exist_ok=True)

    # 1. coverage vs accuracy
    fig, ax = plt.subplots(figsize=(6.4, 4), facecolor=SURF)
    _style(ax)
    xs = [c["coverage"] * 100 for c in curve]
    ys = [c["accuracy_answered"] * 100 for c in curve]
    ax.plot(xs, ys, color=BLUE, linewidth=2)
    ch = min(curve, key=lambda c: abs(c["t"] - chosen_t))
    ax.plot([ch["coverage"] * 100], [ch["accuracy_answered"] * 100], "o", color=BLUE,
            markersize=8, markeredgecolor=SURF, markeredgewidth=2)
    ax.annotate(f"chosen cut-off {chosen_t:.2f}\nanswers {ch['coverage']:.0%}, "
                f"{ch['accuracy_answered']:.0%} of those right",
                (ch["coverage"] * 100, ch["accuracy_answered"] * 100), xytext=(12, -32),
                textcoords="offset points", color=INK2, fontsize=9)
    ax.set_xlabel("Programs the model answers (%) — the rest get \"not sure\"", color=INK2)
    ax.set_ylabel("Answers that are right (%)", color=INK2)
    ax.set_title("Answering less often makes the answers more reliable", color=INK, loc="left",
                 fontsize=11)
    ax.set_xlim(0, 102)
    fig.tight_layout()
    fig.savefig(os.path.join(REPORTS, "p2_not_sure_curve.png"), dpi=160)
    plt.close(fig)

    # 2. recall per bug type, generated vs real
    types = [b for b in BUGS if real["per_type"][b]["support"] >= 5]
    fig, ax = plt.subplots(figsize=(7.6, 4), facecolor=SURF)
    _style(ax)
    x = np.arange(len(types))
    w = 0.38
    a = [synth["per_type"][b]["recall"] * 100 for b in types]
    b_ = [real["per_type"][b]["recall"] * 100 for b in types]
    ax.bar(x - w / 2 - 0.01, a, w, color=BLUE, label="Generated bugs (cross-validation)")
    ax.bar(x + w / 2 + 0.01, b_, w, color=ORANGE, label="Real student bugs (auto-labelled)")
    ax.set_xticks(x)
    ax.set_xticklabels([t.replace("_", "\n") for t in types], fontsize=8, color=INK2)
    ax.set_ylabel("Recall: bugs of this type found (%)", color=INK2)
    ax.set_ylim(0, 105)
    ax.set_title("Most bug types are found less often in real code", color=INK, loc="left",
                 fontsize=11, pad=26)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left",
              bbox_to_anchor=(0, 1.0), ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(REPORTS, "p2_recall_by_type.png"), dpi=160)
    plt.close(fig)

    # 3. confusion matrix (generated single-bug programs)
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blue", ["#fcfcfb", "#cde2fb", "#86b6ef", "#3987e5",
                                                      "#1c5cab", "#0d366b"])
    m = np.array(conf_mat, dtype=float)
    row = m / np.maximum(1, m.sum(axis=1, keepdims=True))
    fig, ax = plt.subplots(figsize=(6.8, 5.8), facecolor=SURF)
    ax.imshow(row, cmap=cmap, vmin=0, vmax=1)
    ax.set_xticks(range(len(conf_labels)))
    ax.set_yticks(range(len(conf_labels)))
    ax.set_xticklabels(conf_labels, rotation=45, ha="right", fontsize=8, color=INK2)
    ax.set_yticklabels(conf_labels, fontsize=8, color=INK2)
    for i in range(len(conf_labels)):
        for j in range(len(conf_labels)):
            if row[i, j] >= 0.02:
                ax.text(j, i, f"{row[i, j]:.0%}", ha="center", va="center", fontsize=7,
                        color="#ffffff" if row[i, j] > 0.5 else INK)
    ax.set_xlabel("Model's top guess", color=INK2)
    ax.set_ylabel("Injected bug", color=INK2)
    ax.set_title("Which bugs get mixed up (generated bugs, one bug each)", color=INK, loc="left",
                 fontsize=11)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(REPORTS, "p2_confusion.png"), dpi=160)
    plt.close(fig)


# ----------------------------------------------------------------------------- main
def main():
    if os.name == "nt":
        sys.exit("This script runs student code with Unix timers, so it needs Linux, Mac or WSL "
                 "(Windows Subsystem for Linux). The app itself (classifier.py) works on Windows.")
    t_start = time.time()
    print("1. loading data and running every program on its tests")
    mutants = load_mutants()
    auto, human = load_real()
    human_files = {(h["problem_id"], h["file"]) for h in human}
    m_out = outcomes_for([(r["code"], r["problem_id"]) for r in mutants],
                         os.path.join(DATA, "outcomes_mutants.json"))
    a_out = outcomes_for([(r["code"], r["problem_id"]) for r in auto],
                         os.path.join(DATA, "outcomes_real.json"))
    h_out = outcomes_for([(r["code"], r["problem_id"]) for r in human],
                         os.path.join(DATA, "outcomes_real.json"))

    m_grams = [C.ngrams(r["code"]) for r in mutants]
    m_dense, dense_names = dense_matrix([r["code"] for r in mutants], m_out)
    y = multi_hot([r["labels"] for r in mutants])
    groups = np.array([r["base_id"] for r in mutants])
    probs_of = np.array([r["problem_id"] for r in mutants])
    m_labels = [r["labels"] for r in mutants]

    # real validation set: auto-labelled, minus the 50 hand-labelled ones, minus "likely" rows
    val = [(r, o) for r, o in zip(auto, a_out) if (r["problem_id"], r["file"]) not in human_files
           and r["confidence"] in ("confirmed", "unknown")]
    v_rows = [r for r, _ in val]
    v_grams = [C.ngrams(r["code"]) for r in v_rows]
    v_dense, _ = dense_matrix([r["code"] for r in v_rows], [o for _, o in val])
    v_labels = [r["labels"] for r in v_rows]
    conf_idx = [i for i, r in enumerate(v_rows) if r["confidence"] == "confirmed"]
    h_grams = [C.ngrams(r["code"]) for r in human]
    h_dense, _ = dense_matrix([r["code"] for r in human], h_out)
    h_labels = [r["labels"] for r in human]

    # leakage: real programs that appear (after renaming) among the training mutants
    mut_norm = {" ".join(C.normalize_tokens(r["code"])) for r in mutants}
    twins = {i for i, r in enumerate(v_rows) if " ".join(C.normalize_tokens(r["code"])) in mut_norm}
    h_twins = [h["file"] for h in human if " ".join(C.normalize_tokens(h["code"])) in mut_norm]
    print(f"   {len(mutants)} training mutants, {len(v_rows)} real validation programs "
          f"({len(conf_idx)} confirmed), {len(human)} hand-labelled; twins: {len(twins)} / "
          f"{len(h_twins)}")

    gkf = GroupKFold(n_splits=5)
    folds = list(gkf.split(m_dense, y, groups))

    def oof(make):
        p = np.zeros(y.shape)
        for tr, te in folds:
            mdl = make().fit([m_grams[i] for i in tr], m_dense[tr], y[tr])
            p[te] = mdl.proba([m_grams[i] for i in te], m_dense[te])
        return p

    print("2. choosing C with 5-fold cross-validation grouped by base program")
    c_scores = {}
    for c in (1.0, 4.0, 16.0):
        c_scores[c] = scores(oof(lambda c=c: Model(c)), m_labels)["macro_f1"]
        print(f"   C={c}: macro-F1 {c_scores[c]:.3f}")
    best_c = max(c_scores, key=c_scores.get)

    print("3. generated-bug results")
    p_oof = oof(lambda: Model(best_c))
    global EXTRA_TAU
    tau_scores = {t: scores(p_oof, m_labels, t)["macro_f1"] for t in (0.5, 0.7, 0.8, 0.9, 0.95)}
    EXTRA_TAU = max(tau_scores, key=tau_scores.get)
    print(f"   second-bug cut-off {EXTRA_TAU} (macro-F1 {tau_scores[EXTRA_TAU]:.3f})")
    synth = scores(p_oof, m_labels)
    single = [i for i, labs in enumerate(m_labels) if len(labs) == 1]
    conf_labels = [b for b in BUGS if sum(m_labels[i] == [b] for i in single) > 0]
    conf_mat = [[0] * len(conf_labels) for _ in conf_labels]
    for i in single:
        t = conf_labels.index(m_labels[i][0])
        top = BUGS[int(np.argmax(p_oof[i]))]
        if top in conf_labels:
            conf_mat[t][conf_labels.index(top)] += 1
    ablation = {}
    for name, kw in (("text only", dict(use_dense=False)), ("behaviour+structure only",
                                                             dict(use_text=False))):
        ablation[name] = {"synthetic": scores(oof(lambda kw=kw: Model(best_c, **kw)), m_labels)}

    print("   leave-one-problem-out")
    fam = np.array([p if p.startswith("nus_") else "toy" for p in probs_of])
    lopo = {}
    for held in sorted(set(fam)):
        if held == "toy":
            continue
        tr = np.where(fam != held)[0]
        te = np.where(fam == held)[0]
        mdl = Model(best_c).fit([m_grams[i] for i in tr], m_dense[tr], y[tr])
        s = scores(mdl.proba([m_grams[i] for i in te], m_dense[te]), [m_labels[i] for i in te])
        ri = [i for i in conf_idx if v_rows[i]["problem_id"] == held]
        rp = mdl.proba([v_grams[i] for i in ri], v_dense[ri])
        rs = scores(rp, [v_labels[i] for i in ri])
        lopo[held] = {"synthetic_top1": s["top1"], "synthetic_macro_f1": s["macro_f1"],
                      "real_confirmed_top1": rs["top1"], "real_n": len(ri)}
        print(f"   held out {held}: generated top-1 {s['top1']:.2f}, real top-1 {rs['top1']:.2f}"
              f" (n={len(ri)})")

    print("4. final model on all generated bugs -> real programs")
    final = Model(best_c).fit(m_grams, m_dense, y)
    pv = final.proba(v_grams, v_dense)
    ph = final.proba(h_grams, h_dense)
    real_conf = scores(pv[conf_idx], [v_labels[i] for i in conf_idx])
    no_twin = [i for i in conf_idx if i not in twins]
    real_conf_no_twins = scores(pv[no_twin], [v_labels[i] for i in no_twin])
    h_bug = [i for i, labs in enumerate(h_labels) if not set(labs) <= {"other"}]
    human_bug = scores(ph[h_bug], [h_labels[i] for i in h_bug])
    for name, kw in (("text only", dict(use_dense=False)), ("behaviour+structure only",
                                                             dict(use_text=False))):
        mdl = Model(best_c, **kw).fit(m_grams, m_dense, y)
        ablation[name]["real_confirmed"] = scores(mdl.proba([v_grams[i] for i in conf_idx],
                                                            v_dense[conf_idx]),
                                                  [v_labels[i] for i in conf_idx])
        ablation[name]["hand_50_bugs"] = scores(mdl.proba([h_grams[i] for i in h_bug],
                                                          h_dense[h_bug]),
                                                [h_labels[i] for i in h_bug])
    ablation["both (final model)"] = {"synthetic": synth, "real_confirmed": real_conf,
                                      "hand_50_bugs": human_bug}

    print("5. 'not sure': a confidence model trained on the real validation programs")
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.metrics import roc_auc_score
    xc = conf_matrix_x(pv, v_dense, dense_names)
    yc = right_or_not(pv, v_labels)
    conf_scaler = StandardScaler().fit(xc)
    conf_lr = LogisticRegression(C=0.1, max_iter=2000)
    # honest numbers: each validation program judged by a confidence model that did not see it
    q_cv = cross_val_predict(LogisticRegression(C=0.1, max_iter=2000), conf_scaler.transform(xc),
                             yc, cv=StratifiedKFold(5, shuffle=True, random_state=SEED),
                             method="predict_proba")[:, 1]
    conf_auc = float(roc_auc_score(yc, q_cv))
    conf_auc_top = float(roc_auc_score(yc, xc[:, 0]))
    curve = []
    for t in np.round(np.arange(0.0, 0.96, 0.025), 3):
        d = decisions(pv, v_labels, q_cv, t)
        d["t"] = float(t)
        curve.append(d)
    chosen = max(curve, key=lambda d: (round(d["decision_accuracy"], 4), d["t"]))
    t_star = chosen["t"]
    conf_lr.fit(conf_scaler.transform(xc), yc)
    xh = conf_matrix_x(ph, h_dense, dense_names)
    qh = conf_lr.predict_proba(conf_scaler.transform(xh))[:, 1]
    hand_dec = decisions(ph, h_labels, qh, t_star)
    hand_dec_none = decisions(ph, h_labels, np.ones(len(ph)), 0.0)
    print(f"   confidence AUC {conf_auc:.3f} (top probability alone {conf_auc_top:.3f}); "
          f"cut-off {t_star}: answers {chosen['coverage']:.0%}, right {chosen['accuracy_answered']:.0%}")
    print(f"   hand-labelled 50: answers {hand_dec['coverage']:.0%}, right "
          f"{hand_dec['accuracy_answered']:.0%}, 'other' caught {hand_dec['other_abstained']}/"
          f"{hand_dec['other']}")

    print("6. baselines")
    common = Counter(b for labs in m_labels for b in labs).most_common(1)[0][0]
    base = {}
    for name, fn in (("always '" + common + "'", lambda r: common),
                     ("app.py text rules (before the model)",
                      lambda r: app_rules(r["code"], r["problem_id"]))):
        pc = np.array([fixed_guess_p(fn(v_rows[i])) for i in conf_idx])
        phb = np.array([fixed_guess_p(fn(human[i])) for i in h_bug])
        base[name] = {"real_confirmed_top1": scores(pc, [v_labels[i] for i in conf_idx])["top1"],
                      "hand_50_bugs_top1": scores(phb, [h_labels[i] for i in h_bug])["top1"]}

    # export + check the pure-python copy gives the same answers as scikit-learn
    info = {"mutants": len(mutants), "base_programs": int(len(set(groups))), "C": best_c,
            "date": time.strftime("%Y-%m-%d")}
    model_json = final.export(dense_names, t_star, info)
    model_json["extra_bug_threshold"] = EXTRA_TAU
    model_json["confidence_model"] = {
        "names": C.CONF_NAMES, "coef": [round(float(w), 6) for w in conf_lr.coef_.ravel()],
        "intercept": round(float(conf_lr.intercept_[0]), 6),
        "mean": [round(float(v), 6) for v in conf_scaler.mean_],
        "scale": [round(float(v), 6) if v > 0 else 1.0 for v in conf_scaler.scale_]}
    os.makedirs("models", exist_ok=True)
    with open(os.path.join("models", "bug_classifier.json"), "w", encoding="utf-8") as f:
        json.dump(model_json, f, separators=(",", ":"))
    loaded = C.load_model(os.path.join("models", "bug_classifier.json"))
    diffs = []
    for i in range(0, len(human)):
        mine = C.probabilities(human[i]["code"], h_out[i], loaded)
        diffs.append(max(abs(mine[b] - ph[i][k]) for k, b in enumerate(BUGS)))
        mine_c = C.confidence(mine, C.dense_features(human[i]["code"], h_out[i]), loaded)
        diffs.append(abs(mine_c - qh[i]))
    parity = float(max(diffs))
    print(f"   pure-python model vs scikit-learn: largest difference {parity:.2e}")

    metrics = {"best_C": best_c, "C_search": c_scores, "synthetic": synth, "lopo": lopo,
               "real_confirmed": real_conf, "real_confirmed_no_twins": real_conf_no_twins,
               "twins_in_validation": len(twins), "twins_in_hand_50": h_twins,
               "hand_50_bugs": human_bug, "hand_50_decisions": hand_dec,
               "hand_50_decisions_no_abstain": hand_dec_none, "not_sure_threshold": t_star,
               "validation_decisions": chosen, "baselines": base, "curve": curve,
               "confidence_auc": conf_auc, "confidence_auc_top_prob": conf_auc_top,
               "extra_bug_threshold": EXTRA_TAU, "tau_search": tau_scores, "ablation": ablation,
               "parity_max_diff": parity, "counts": {
                   "mutants": len(mutants), "validation": len(v_rows), "confirmed": len(conf_idx),
                   "validation_other": sum(1 for l in v_labels if set(l) <= {"other"}),
                   "hand": len(human), "hand_bug": len(h_bug)},
               "hand_50_predictions": [
                   {"file": human[i]["file"], "human": h_labels[i],
                    "top": BUGS[int(np.argmax(ph[i]))], "confidence": round(float(qh[i]), 3),
                    "not_sure": bool(qh[i] < t_star)}
                   for i in range(len(human))]}
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "p2_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=1)
    figures(curve, t_star, synth, real_conf, conf_mat, conf_labels)
    write_report(metrics)
    print(f"done in {time.time() - t_start:.0f}s -> models/bug_classifier.json, "
          f"{REPORTS}/p2_results.md")


def pct(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.0f}%"


def write_report(m):
    s, rc, h = m["synthetic"], m["real_confirmed"], m["hand_50_bugs"]
    hd, hn, vd = m["hand_50_decisions"], m["hand_50_decisions_no_abstain"], m["validation_decisions"]
    L = []
    L.append("# P2 bug-type classifier: results\n")
    L.append(f"Model: one logistic regression per bug type (C = {m['best_C']}), on TF-IDF of the "
             "normalized code plus test-behaviour and structure features. Trained on "
             f"{m['counts']['mutants']} generated bugs. Generated by `scripts/train.py`.\n")
    L.append("## Headline\n")
    L.append("| Test set | Programs | Top guess is right |")
    L.append("|---|---|---|")
    L.append(f"| Generated bugs, 5-fold CV split by base program | {s['n']} | {pct(s['top1'])} |")
    L.append(f"| Real programs, auto-labelled (confirmed by a passing fix) | {rc['n']} | "
             f"{pct(rc['top1'])} |")
    L.append(f"| Real programs, hand-labelled by Krishna (bug cases only) | {h['n']} | "
             f"{pct(h['top1'])} |\n")
    L.append("## Against the baselines (top guess is right)\n")
    L.append("| Method | Real, auto-labelled | Real, hand-labelled |")
    L.append("|---|---|---|")
    for name, b in m["baselines"].items():
        L.append(f"| {name} | {pct(b['real_confirmed_top1'])} | {pct(b['hand_50_bugs_top1'])} |")
    L.append(f"| **trained model** | **{pct(rc['top1'])}** | **{pct(h['top1'])}** |\n")
    L.append("## Per bug type\n")
    L.append("Recall = share of the bugs of that type the model finds. Precision = share of the "
             "model's claims of that type that are right.\n")
    L.append("| Bug type | Generated: precision | Generated: recall | Real auto-labelled: recall (n) |")
    L.append("|---|---|---|---|")
    for b in BUGS:
        g, r = s["per_type"][b], rc["per_type"][b]
        L.append(f"| {b} | {pct(g['precision'])} | {pct(g['recall'])} | "
                 f"{pct(r['recall']) if r['support'] else 'n/a'} ({r['support']}) |")
    L.append(f"\nGenerated bugs: macro-F1 {s['macro_f1']:.2f}, exact label set right "
             f"{pct(s['exact'])}.\n")
    L.append("## New problem test (train on 4 NUS problems, test on the 5th)\n")
    L.append("| Held-out problem | Generated: top-1 | Real confirmed: top-1 (n) |")
    L.append("|---|---|---|")
    for p, d in m["lopo"].items():
        L.append(f"| {p} | {pct(d['synthetic_top1'])} | {pct(d['real_confirmed_top1'])} "
                 f"({d['real_n']}) |")
    L.append("")
    L.append("## Which evidence matters (ablation)\n")
    L.append("| Features | Generated: top-1 | Real auto-labelled: top-1 | Real hand-labelled: top-1 |")
    L.append("|---|---|---|---|")
    for name, a in m["ablation"].items():
        L.append(f"| {name} | {pct(a['synthetic']['top1'])} | {pct(a['real_confirmed']['top1'])} "
                 f"| {pct(a['hand_50_bugs']['top1'])} |")
    L.append("")
    L.append("## \"Not sure\"\n")
    L.append("A small second model (logistic regression on how far ahead the top bug type is, "
             "plus how the program behaved on the tests) estimates whether the top guess is right. "
             f"It was trained on the {m['counts']['validation']} auto-labelled real programs (never "
             "on the hand-labelled 50). Judged by 5-fold cross-validation it separates right from "
             f"wrong guesses with AUC **{m['confidence_auc']:.2f}** (the top probability alone: "
             f"{m['confidence_auc_top_prob']:.2f}). Cut-off: **{m['not_sure_threshold']:.3f}**; "
             "below it the app gives a general hint instead of naming a bug type.\n")
    L.append("| | Validation (auto-labelled) | Hand-labelled 50 |")
    L.append("|---|---|---|")
    L.append(f"| Programs answered | {pct(vd['coverage'])} | {pct(hd['coverage'])} |")
    L.append(f"| Answers that are right | {pct(vd['accuracy_answered'])} | "
             f"{pct(hd['accuracy_answered'])} |")
    L.append(f"| 'other' programs where the model said not sure | {vd['other_abstained']}/"
             f"{vd['other']} | {hd['other_abstained']}/{hd['other']} |")
    L.append(f"| Without 'not sure': answers right | - | {pct(hn['accuracy_answered'])} |\n")
    L.append("## Honesty notes\n")
    L.append(f"- Auto-labels come from the same kind of edits used to generate training bugs, so "
             "the auto-labelled score is optimistic; the hand-labelled 50 is the fairer test.")
    L.append(f"- {m['twins_in_validation']} validation programs are identical (after renaming) to "
             f"a training mutant; without them, real auto-labelled top-1 is "
             f"{pct(m['real_confirmed_no_twins']['top1'])}. Hand-labelled programs with a twin: "
             f"{len(m['twins_in_hand_50'])}.")
    L.append("- The hand-labelled set is small (50, of which "
             f"{m['counts']['hand_bug']} have a named bug), so its numbers move by 2-3 points per "
             "program.")
    L.append(f"- The JSON model used by the app matches scikit-learn to within "
             f"{m['parity_max_diff']:.1e}.\n")
    L.append("Figures: `p2_not_sure_curve.png`, `p2_recall_by_type.png`, `p2_confusion.png`.\n")
    with open(os.path.join(REPORTS, "p2_results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
