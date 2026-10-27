"""find_suspicious_lines: spectrum-based fault localization (check 4 in the plan).

Idea: record which lines run in each test. A line that runs in many FAILING tests and
few PASSING tests is suspicious. Two standard scores are provided:

  ochiai    = f / sqrt(F * (f + p))
  tarantula = (f/F) / ((f/F) + (p/P))

where F, P = number of failing / passing tests, and f, p = how many failing / passing
tests executed this line.

Return format:  [{"line": int, "score": float}, ...]  sorted by score, highest first.
Line numbers are 1-based and refer to the student's code.
An empty list means there is nothing to localize (code passed, or has a syntax error).
"""
import math
from collections import defaultdict

from runner import run_code


def score_lines(tests, formula="ochiai"):
    failing = [t for t in tests if not t["passed"]]
    passing = [t for t in tests if t["passed"]]
    F, P = len(failing), len(passing)
    if F == 0:
        return []

    f_count, p_count = defaultdict(int), defaultdict(int)
    for t in failing:
        for ln in set(t.get("lines", [])):
            f_count[ln] += 1
    for t in passing:
        for ln in set(t.get("lines", [])):
            p_count[ln] += 1

    scored = []
    for ln in set(f_count) | set(p_count):
        f, p = f_count[ln], p_count[ln]
        if f == 0:
            score = 0.0
        elif formula == "tarantula":
            fr = f / F
            pr = p / P if P else 0.0
            score = fr / (fr + pr)
        else:
            score = f / math.sqrt(F * (f + p))
        scored.append({"line": ln, "score": round(score, 4)})
    scored.sort(key=lambda d: (-d["score"], d["line"]))
    return scored


def find_suspicious_lines(code, problem_id, formula="ochiai"):
    res = run_code(code, problem_id, trace=True)
    if res["status"] != "failed":
        return []
    return score_lines(res["tests"], formula)
