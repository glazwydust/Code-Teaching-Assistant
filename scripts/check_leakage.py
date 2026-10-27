"""How close are the real WRONG programs (our test set) to the CORRECT programs (P2's training base)?

P2 plans to break correct programs to make training data and test on real wrong programs. If a
wrong program is a near copy of a correct one (e.g. the same student fixed one character in the
next attempt), then training on the broken correct program is almost training on the test program.
This measures that risk. It compares programs after renaming all variables and ignoring
comments/whitespace.

Run from the codeEd folder (one command):

    py scripts/check_leakage.py --max-wrong 300

  --max-wrong N   wrong programs checked per question (0 = all; the full run can take several minutes)
  --csv FILE      per-program results (default leakage_report.csv)

How to read it: the share of wrong programs with a near-twin (similarity >= 0.95) among the correct
ones. If it is large, report the "train on 4 problems, test on the 5th" result as the headline, or
drop the twins listed in the CSV from P2's training data.
"""
import argparse
import builtins
import csv
import io
import keyword
import os
import random
import sys
import tokenize
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nus  # noqa: E402

_BUILTINS = set(dir(builtins))


def normalize(code):
    """Token list with identifiers renamed to ID and strings to STR (comments/whitespace dropped)."""
    toks = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(code).readline):
            if t.type == tokenize.NAME:
                s = t.string
                toks.append(s if keyword.iskeyword(s) or s in _BUILTINS else "ID")
            elif t.type == tokenize.NUMBER:
                toks.append(t.string)
            elif t.type == tokenize.STRING:
                toks.append("STR")
            elif t.type == tokenize.OP:
                toks.append(t.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return code.split()
    return toks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="refactory_data/data")
    ap.add_argument("--questions", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--max-wrong", type=int, default=300)
    ap.add_argument("--csv", default="leakage_report.csv")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = []
    for q in args.questions:
        try:
            problem = nus.load_question(args.data, q)
        except (ValueError, OSError) as exc:
            print(f"question_{q}: skipped ({exc})")
            continue
        correct = [(p, normalize(nus.program_code(p))) for p in nus.list_programs(problem, "correct")]
        wrong = nus.list_programs(problem, "wrong", limit=args.max_wrong or None, seed=args.seed)
        unique = len({tuple(t) for _, t in correct})
        print(f"question_{q}: {len(correct)} correct programs ({unique} different after renaming "
              f"variables), checking {len(wrong)} wrong programs ...", flush=True)

        sims = []
        for wp in wrong:
            wt = normalize(nus.program_code(wp))
            sm = SequenceMatcher(None, autojunk=False)
            sm.set_seq2(wt)                       # seq2 is cached by difflib, so set it once
            best, best_path = 0.0, ""
            for cp, ct in correct:
                sm.set_seq1(ct)
                if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
                    continue
                r = sm.ratio()
                if r > best:
                    best, best_path = r, cp
            sims.append(best)
            rows.append({"question": q, "wrong_file": os.path.basename(wp),
                         "nearest_correct": os.path.basename(best_path), "similarity": round(best, 3)})
        n = len(sims) or 1
        print("   near-twin among correct programs:  >=0.95: %.0f%%   >=0.90: %.0f%%   >=0.80: %.0f%%\n" % (
            100 * sum(s >= 0.95 for s in sims) / n, 100 * sum(s >= 0.90 for s in sims) / n,
            100 * sum(s >= 0.80 for s in sims) / n), flush=True)

    if rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        n = len(rows)
        print("ALL QUESTIONS: >=0.95: %.0f%%   >=0.90: %.0f%%   >=0.80: %.0f%%   (details: %s)" % (
            100 * sum(r["similarity"] >= 0.95 for r in rows) / n,
            100 * sum(r["similarity"] >= 0.90 for r in rows) / n,
            100 * sum(r["similarity"] >= 0.80 for r in rows) / n, args.csv))


if __name__ == "__main__":
    main()
