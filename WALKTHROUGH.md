# codeEd P1: Complete Walkthrough

> **Update (Oct 28): time limits changed.** `runner.py` no longer has `PER_TEST_TIMEOUT = 1.0` and
> `TOTAL_TIMEOUT = 5.0`. A test now gets 2 seconds (`PER_TEST_TIMEOUT`, including Python start-up),
> and only tests that actually time out count against a 4-second `TIMEOUT_BUDGET`; after that the
> remaining tests are skipped. Reason: starting a Python process per test is slow on Windows, and the
> old 5-second total ran out on question 2 (17 tests), marking correct programs as timeouts. Where
> this guide says 1 s / 5 s, read 2 s / 4 s. The quiz answer for question 4 changes the same way.


Read this top to bottom once. After that, you should be able to explain any file to a teacher.

---

## 0. Your role

I wrote this code because you asked me to. That does not remove your role. In the team plan, P1 is the **owner** of this part, which means:

1. **Understand it** well enough to explain and defend it (this file is for that).
2. **Verify it**: run `py self_test.py` and try your own buggy programs on it.
3. **Hand it over**: push to GitHub and make sure P2, P3 and P4 can call your two functions.
4. **Maintain it**: when a teammate finds a problem, you fix it or fix the tests.
5. **Present it**: answer questions about the runner and the suspicious-line tool in the demo.

Check your course's rules on AI-assisted code. If disclosure is required, say that Claude helped write P1.

---

## 1. The big picture

```
 student code (text)  +  problem id ("sum_list")
          |
          v
   runner.run_code()
     1. check_syntax        -> is it valid Python?          (Python's parser)
     2. find_undefined_names-> any name used but not defined? (pyflakes / fallback)
     3. for each test:  start a NEW Python process (_worker.py), run the code, collect result
          |
          v
   {status, tests:[{input, expected, actual, passed, error}]}
          |
          v   (if some tests failed)
   localize.find_suspicious_lines()
     - runs the tests again with line tracing on
     - counts which lines ran in failing vs passing tests
     - scores every line (Ochiai) and sorts
          |
          v
   [{line: 3, score: 0.8452}, {line: 2, score: 0.8452}, ...]
```

What each file is for:

| File | Purpose |
|---|---|
| `problems/__init__.py` | Registry: the list of problem ids and bug types, and a function to load one problem |
| `problems/<id>.py` (5 files) | One problem each: statement, tests, 4 correct solutions, 2 buggy examples |
| `_worker.py` | Runs ONE test of the student's code in its own process, so a crash or infinite loop can't harm the website |
| `runner.py` | `run_code`: syntax check, undefined-name check, runs every test through `_worker.py` with time limits |
| `localize.py` | `find_suspicious_lines`: ranks lines by how likely they are to be wrong |
| `self_test.py` | Automatic checks that all of the above works |
| `scripts/get_mbpp.py` | Optional: downloads the MBPP dataset from Hugging Face |
| `requirements.txt` | Optional packages |
| `README.md` | The contract (function formats) for your teammates |

---

## 2. `problems/__init__.py`

**Purpose:** one place that knows which problems exist, so every other file asks it instead of hard-coding names.

```python
from importlib import import_module
```
`import_module("x")` imports a module whose name is stored in a string. We need that because the problem id (`"sum_list"`) arrives as text.

```python
PROBLEM_IDS = ["sum_list", "factorial", "max_in_list", "count_evens", "is_palindrome"]
```
The 5 problems. The names are the contract with the team.

```python
BUG_TYPES = ["off_by_one", "wrong_operator", "missing_base_case", "wrong_init", "missing_return"]
```
The 5 bug types from the plan. P2's model will predict these exact names, so everyone must spell them the same way.

```python
_cache = {}
```
A dictionary remembering problems that are already loaded. The leading `_` means "internal, don't use from outside".

```python
def get_problem(problem_id):
    if problem_id not in PROBLEM_IDS:
        raise KeyError(f"Unknown problem '{problem_id}'. Choose from {PROBLEM_IDS}")
```
If someone asks for a problem that doesn't exist, stop with a clear error message.

```python
    if problem_id not in _cache:
        _cache[problem_id] = import_module(f"problems.{problem_id}").PROBLEM
    return _cache[problem_id]
```
If not loaded yet, import `problems/<id>.py` and take its `PROBLEM` dictionary. Store it, then return it.

```python
def all_problems():
    return [get_problem(pid) for pid in PROBLEM_IDS]
```
A list of all 5 problem dictionaries, used by the self-test.

---

## 3. A problem file (`problems/sum_list.py` as the example)

The other four have the same structure, so learn this one.

```python
PROBLEM = {
    "id": "sum_list",
    "function": "sum_list",
```
`id` is the registry name. `function` is the name the student's function must have, because the runner calls it by name.

```python
    "title": ..., "description": ...,
```
Student-facing text for the website. The description states the rules (e.g. "do not use sum()").

```python
    "tests": [
        ([[]], 0),
        ([[5]], 5),
        ([[1, 2, 3]], 6),
        ...
    ],
```
Each test is `(args, expected)`. **`args` is the list of arguments**, so `[[1, 2, 3]]` means "call the function with ONE argument, the list `[1, 2, 3]`". That is why there are double brackets. The tests are deliberately tricky: empty list, one item, negatives, zeros, values that cancel (`[10, -10, 10]`). These edge cases are what separate a correct program from one that only works on easy inputs.

```python
    "solutions": [ "def sum_list(nums):\n    total = 0\n    ...", ... ]
```
Four correct solutions **written as text strings**, with `\n` for new lines. They are strings because the runner treats any program, correct or student-written, as text it can execute. The styles differ (for-each loop, index loop, while loop, recursion) to prove that any correct style passes.

```python
    "buggy_examples": [
        {"name": "loop_stops_early", "bug_type": "off_by_one",
         "code": "...", "bug_lines": [3]},
```
Hand-made wrong programs. `bug_type` says which of the 5 categories it belongs to. `bug_lines` is the true buggy line (counting from 1), so we can check whether the localizer finds it. P2 can use these as seeds for the training data.

Per-problem differences: `factorial` has a `from functools import reduce` solution; `is_palindrome` expects `True`/`False`; its "missing return" example points at the last line because the bug is something that is absent.

---

## 4. `_worker.py` (the sandbox)

**Purpose:** run **one test** of the student's code inside a separate Python process. If the student's code crashes, loops forever, or eats memory, only this small process suffers. The parent (`runner.py`) just kills it.

It **reads JSON from stdin** and **prints JSON to stdout**. That is the only way the two processes talk.

```python
import copy, io, json, sys
```
`copy` for safe copies, `io` for a fake output buffer, `json` for talking to the parent, `sys` for stdin/stdout and tracing.

```python
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
except Exception:
    pass
```
Limit this process to 512 MB of memory. The `resource` module only exists on Linux/Mac, so on Windows the import fails and `except` quietly skips it. That's why the memory cap doesn't apply on your laptop.

```python
STUDENT_FILE = "<student>"
```
A fake filename given to the student's code. Later the tracer uses it to tell the student's lines apart from our own.

```python
def jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)
```
We must send the student's answer back as JSON. If it can't be converted (e.g. a weird object), send its text form `repr(value)` instead.

```python
def main():
    real_stdout = sys.stdout
    payload = json.loads(sys.stdin.read())
    out = {"passed": False, "actual": None, "error": None, "lines": []}
    seen = set()
```
Remember the real output channel, read the request from the parent, and prepare the answer. `seen` will collect line numbers that ran. A `set` automatically ignores duplicates.

```python
    def tracer(frame, event, arg):
        if frame.f_code.co_filename != STUDENT_FILE:
            return None
        if event == "line":
            seen.add(frame.f_lineno)
        return tracer
```
This is the line-recorder. When tracing is on, Python calls `tracer` at events such as "a function was called" or "a new line is about to run". The first `if` ignores any code that isn't the student's. If the event is `"line"`, store the line number (`f_lineno`). `return tracer` means "keep tracing inside this function".

```python
    sys.stdout = io.StringIO()
```
Student `print()` calls would otherwise mix with our JSON and corrupt it. This redirects all printing into a throwaway buffer.

```python
    try:
        namespace = {"__name__": "student_module"}
        exec(compile(payload["code"], STUDENT_FILE, "exec"), namespace)
```
`compile` turns the student's text into runnable code, labeled with the fake filename. `exec` runs it inside an empty `namespace` dictionary. Running a file that contains `def sum_list(...)` just **defines** the function and stores it in `namespace`.

```python
        func = namespace.get(payload["func"])
        if not callable(func):
            out["error"] = f"Function '{payload['func']}' is not defined"
```
Fetch the function by name. If the student never defined it (or used a different name), report that.

```python
        else:
            args = copy.deepcopy(payload["args"])
            if payload.get("trace"):
                sys.settrace(tracer)
            try:
                actual = func(*args)
            finally:
                sys.settrace(None)
```
Copy the arguments, switch the line-recorder on only if asked, and **call the function** (`*args` unpacks the list into separate arguments). `finally` guarantees tracing turns off even if the function crashes. Turning it on only around the call means we record lines of the function body and nothing else.

```python
            out["actual"] = jsonable(actual)
            expected = payload["expected"]
            out["passed"] = (actual == expected) and (type(actual) is type(expected))
```
Store the answer, then decide pass/fail. The **type check** matters: in Python `True == 1` and `0 == False`, so without it a function returning `1` would wrongly pass a test expecting `True`. Also `None` (a missing return) can never equal `False`.

```python
    except BaseException as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
```
Any error in the student's code (IndexError, ZeroDivisionError, RecursionError, even `sys.exit()`) is caught and written as e.g. `"IndexError: list index out of range"`. `BaseException` is the widest net.

```python
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout
        out["lines"] = sorted(seen)
    real_stdout.write(json.dumps(out))
```
Always: turn tracing off, restore real output, store the recorded lines in order. Finally print the JSON answer for the parent to read.

```python
if __name__ == "__main__":
    main()
```
Run `main()` only when this file is started directly (which is how the parent starts it).

---

## 5. `runner.py` (`run_code`)

**Purpose:** the manager. It checks the code, launches workers, collects results, and builds the dictionary the team agreed on.

```python
HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "_worker.py")
```
Find the folder this file lives in, then build the full path to `_worker.py`. This makes it work no matter where you run Python from.

```python
PER_TEST_TIMEOUT = 1.0
TOTAL_TIMEOUT = 5.0
```
A single test gets 1 second (our tests are tiny, so anything slower is almost surely an infinite loop). The whole submission gets 5 seconds, which is the limit from the plan.

### `check_syntax`
```python
try:
    ast.parse(code)
except SyntaxError as exc:
    return {"line": exc.lineno or 1, "message": exc.msg}
return None
```
`ast.parse` reads the code the way Python does, **without running it**. If the code is badly formed (missing bracket, bad indentation), Python raises `SyntaxError`, which carries the line number and a message. Returns `None` when the code is fine.

### `find_undefined_names`
Finds names that are used but never created, e.g. a typo like `totl`.

- First it tries **pyflakes**, a well-known code checker: `checker.Checker(tree).messages` lists problems, and we keep only those of type `UndefinedName`.
- If pyflakes isn't installed (`except ImportError`), a **fallback** does it by hand:
  1. Start `defined` with every Python built-in (`dir(builtins)`).
  2. Walk the whole code tree (`ast.walk`) and add every name that gets assigned, every function or class name, every parameter, every imported name, and every `except ... as name`.
  3. Report any name that is *read* (`ast.Load`) but isn't in `defined`.
- It's "scope-blind": it doesn't track which function a variable belongs to. That's good enough for beginner programs, but not perfect.

### `_run_one`
```python
payload = json.dumps({"code": code, "func": func, "args": args, "expected": expected, "trace": trace})
proc = subprocess.run([sys.executable, "-I", WORKER], input=payload,
                      capture_output=True, text=True, timeout=timeout)
```
Package the request as JSON and start a **brand new Python process** running `_worker.py`:
- `sys.executable` is the same Python that's running us.
- `-I` is *isolated mode*: the worker ignores environment variables and the current folder, which blocks accidental or malicious imports.
- `input=payload` sends our JSON to the worker's stdin.
- `capture_output=True` collects what it prints.
- `timeout=timeout` kills the worker if it runs too long.

```python
except subprocess.TimeoutExpired:
    return {... "error": "Timed out (possible infinite loop)"}
```
That's how an infinite loop is handled. The process is killed and we report a timeout.

```python
try:
    return json.loads(proc.stdout)
except ValueError:
    err = (proc.stderr.strip().splitlines() or ["Program crashed"])[-1]
    return {... "error": err}
```
Normally the worker's output is valid JSON. If not (a hard crash, such as running out of memory), take the last line of its error text as the error message.

### `run_code`
1. Build `result` with defaults: status `"failed"`, `num_total` equal to the number of tests.
2. `check_syntax`: if it found an error, set `status = "syntax_error"`, store it and **return immediately**. There is no point running broken code.
3. `result["warnings"] = find_undefined_names(code)`. These are warnings, not stoppers.
4. `deadline = time.monotonic() + TOTAL_TIMEOUT`. `monotonic` is a clock that never jumps backwards.
5. For each test: compute `remaining` time. If none is left, record `"Skipped: total time limit reached"`. Otherwise call `_run_one` with `min(PER_TEST_TIMEOUT, remaining)`, so no test exceeds either limit.
6. Build the entry `{input, expected, actual, passed, error}`. If `trace=True`, also add `"lines"`.
7. `num_passed = sum(t["passed"] for t in ...)`. Adding booleans counts the `True` ones. If every test passed, status is `"passed"`, else `"failed"`.

The `if __name__ == "__main__":` block at the bottom lets you test from the terminal: `py runner.py sum_list my_attempt.py`.

---

## 6. `localize.py` (`find_suspicious_lines`)

**Purpose:** answer "which line is most likely wrong?" using **spectrum-based fault localization**.

**The idea:** a line that runs in many *failing* tests and few *passing* tests is suspicious.

**Ochiai score:**  `score = f / sqrt(F * (f + p))`
- `F` = number of failing tests, `P` = number of passing tests
- `f` = failing tests that ran this line, `p` = passing tests that ran it

**Worked example** (the "loop stops early" program, which uses `range(len(nums) - 1)`):
```
1 def sum_list(nums):
2     s = 0
3     for i in range(len(nums) - 1):     <- the bug
4         s += nums[i]
5     return s
```
5 tests fail and 2 pass, so F = 5 and P = 2.
- Lines 2, 3, 5 run in all 7 tests: f = 5, p = 2, so score = 5 / sqrt(5 x 7) = **0.8452**
- Line 4 runs only when the loop executes at least once: f = 3, p = 1, so score = 3 / sqrt(5 x 4) = **0.67**

So lines 2, 3 and 5 **tie** for the top (this is the "tied with 2 others" in your test output), and line 4 comes lower. This is a real limitation: the bug is in the *loop range*, but because that line runs in every test, the tool can't single it out. That is why the plan adds other evidence (the model, trace comparison, and repair search).

### The code

```python
failing = [t for t in tests if not t["passed"]]
passing = [t for t in tests if t["passed"]]
F, P = len(failing), len(passing)
if F == 0:
    return []
```
Split the tests into failing and passing. If nothing failed, there's nothing to blame, so return an empty list.

```python
f_count, p_count = defaultdict(int), defaultdict(int)
for t in failing:
    for ln in set(t.get("lines", [])):
        f_count[ln] += 1
```
`defaultdict(int)` is a dictionary where missing keys start at 0. For each failing test, add 1 to every line it ran. `set(...)` makes sure a line counts **once per test**, even if a loop ran it many times. The same is done for passing tests.

```python
for ln in set(f_count) | set(p_count):
```
Every line that appeared anywhere (`|` joins two sets).

```python
if f == 0:                       score = 0.0
elif formula == "tarantula":     fr = f / F;  pr = p / P if P else 0.0;  score = fr / (fr + pr)
else:                            score = f / math.sqrt(F * (f + p))
```
A line that never ran in a failing test can't be the culprit: score 0. Tarantula is an alternative scoring formula. Otherwise use Ochiai.

```python
scored.sort(key=lambda d: (-d["score"], d["line"]))
```
Sort by score, highest first (the minus sign reverses it). Ties are ordered by line number.

```python
def find_suspicious_lines(code, problem_id, formula="ochiai"):
    res = run_code(code, problem_id, trace=True)
    if res["status"] != "failed":
        return []
    return score_lines(res["tests"], formula)
```
Run the tests with tracing on. If the code passed or had a syntax error, return `[]`. Otherwise score the lines.

---

## 7. `self_test.py`

**Purpose:** proof that everything works, so you don't have to take my word for it. It exits with an error if any check fails.

- `failures = []` and `check(cond, msg)`: a tiny helper. If `cond` is False it records and prints a failure.
- **Section A:** for every problem, run each of the 4 correct solutions and require status `"passed"`. `assert len(p["solutions"]) == 4` also checks the count.
- **Section B:** run every buggy example and require status `"failed"`. If a "buggy" program secretly passed, P2's data would be wrong.
- **Section C:** compute the rank of the true bug line.
  - `by_line` maps each line to its score.
  - `best_bug` is the best score among the true bug lines.
  - `rank = 1 + number of lines with a strictly higher score`. A tie therefore shares the best rank.
  - `ties` counts other lines with the same score.
  - The summary prints how many bug lines were rank 1 and within the top 3.
- **Section D (safety):** feeds deliberately bad programs: a missing bracket (`def sum_list(nums:`), a typo name (`totl`), `while True: pass` (it checks it stops in under 8 seconds), `nums[99]` (a crash), a program that prints something, and a file that never defines the function. Each must be handled without breaking the runner.
- **The end:** if `failures` is non-empty, `sys.exit(1)`, otherwise "ALL CHECKS PASSED".

---

## 8. Other files

**`scripts/get_mbpp.py`**: downloads MBPP with `load_dataset("google-research-datasets/mbpp", "full", split=...)` for its 4 splits, saves everything as one JSON line per problem in `data/mbpp.jsonl`, then prints the first 3 problems whose description contains your keywords (`"palindrome"`, `"factorial"` and so on). It's optional, and the results showed MBPP doesn't contain simple versions of your 5 problems.

**`requirements.txt`**: lists optional packages (`pyflakes`, `datasets`). The core P1 code uses only Python's standard library.

**`README.md`**: the contract for P2, P3 and P4: the exact shape of what `run_code` and `find_suspicious_lines` return, the problem ids, the bug-type names, the safety limits, and the known limitations.

---

## 9. Things to be honest about (good for your report)

1. **Spectrum-based ranking is a hint, not a verdict.** In the test, the true bug line was ranked first (ties count) in 7 of 10 examples and in the top 3 in all 10.
2. **The "no built-in" rules aren't enforced.** The statement says "don't use `sum()`", but the runner doesn't block it. A student could use it and still pass.
3. **The sandbox is basic.** It uses a separate process plus time and memory limits (memory limit on Linux only). It is not a security-hardened sandbox, so don't run untrusted code from the internet without a container.
4. **The undefined-name check is weaker without pyflakes.** Install pyflakes (`py -m pip install pyflakes`) for the better version.
5. **The tests are written by us.** A student's correct solution that handles something our tests don't cover could pass when it shouldn't, so tricky tests matter.

---

## 10. Quick self-quiz (can you answer these?)

1. Why does each test run in a separate process?
2. Why is `[[1, 2, 3]]` written with double brackets in a test?
3. Why do we check the type as well as the value of the answer?
4. Why are there two timeouts (1s and 5s)?
5. In the worked example, why do lines 2, 3 and 5 tie, and what does that tell you about the method?
6. What happens if the student's code has a syntax error?
7. Why is `-I` used when starting the worker?

Answers: 1) crash or infinite loop can't harm the site; 2) outer list is the argument list, inner list is the single argument; 3) `True == 1` in Python, and `None` must not pass for `False`; 4) a stuck test shouldn't eat the budget of the others, and the total keeps the 5-second limit; 5) they ran in every test, so they carry no extra information, which is why the plan adds more evidence; 6) it returns immediately with status `"syntax_error"` and the line number, and no tests run; 7) isolated mode ignores the environment and the current folder, which blocks accidental or malicious imports.
