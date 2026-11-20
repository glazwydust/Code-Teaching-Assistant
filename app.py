import ast
import streamlit as st

try:
    from runner import run_code
except ImportError:
    run_code = None

try:
    from repair import try_repairs
except ImportError:
    try_repairs = None

try:
    from localize import localize_bug
except ImportError:
    localize_bug = None


PROBLEM_SPECS = {
    "factorial": {
        "func_name": "factorial",
        "label": "Factorial (`factorial(n)`)",
    },
    "is_palindrome": {
        "func_name": "is_palindrome",
        "label": "Palindrome (`is_palindrome(s)`)",
    },
    "count_evens": {
        "func_name": "count_evens",
        "label": "Count Evens (`count_evens(nums)`)",
    },
    "max_in_list": {
        "func_name": "max_in_list",
        "label": "Max in List (`max_in_list(nums)`)",
    },
    "sum_list": {
        "func_name": "sum_list",
        "label": "Sum List (`sum_list(nums)`)",
    },
}

BUG_HINTS = {
    "off_by_one": (
        "Check your loop start/stop boundaries and comparison operators (`<` vs `<=`). "
        "Trace the first and last iteration on a small input to make sure you aren't skipping an element or going one step too far."
    ),
    "wrong_init": (
        "Look closely at the starting value you assign to your accumulator or tracking variable before the loop runs. "
        "Test what happens on an empty input or when all values are negative/zero."
    ),
    "missing_base_case": (
        "Check how your function stops or handles the smallest or empty input first. "
        "Verify that recursion has a clear stopping condition that moves closer to the base case on every call."
    ),
    "wrong_return_indent": (
        "Check the indentation of your `return` statement. "
        "Make sure it isn't exiting early inside a loop or `if`/`else` branch before your logic finishes."
    ),
    "wrong_condition": (
        "Review your `if` or `while` condition logic and boolean operators (`and`, `or`, `not`). "
        "Test boundary cases where two conditions could overlap or miss a valid path."
    ),
    "early_return": (
        "Look for a `return` inside a loop or conditional block that stops execution on the first item instead of checking all items. "
        "Ensure you have inspected every needed element before returning a final answer."
    ),
    "type_or_indexing_error": (
        "Check variable types, string/list lengths, and index bounds before accessing elements by position. "
        "Test edge cases like empty sequences or single-character/single-element inputs."
    ),
    "default": (
        "Trace your function step by step on a small example and compare what each variable holds against what you expect. "
        "Check edge cases such as empty inputs, zero, or boundary values."
    ),
}


def has_defined_function(code: str, expected_func_name: str) -> tuple[bool, str | None]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError on line {exc.lineno}: {exc.msg}"

    defined_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return (expected_func_name in defined_funcs), None


def format_hint(bug_type: str | None, raw_hint: str | None) -> str:
    normalized = (bug_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in BUG_HINTS:
        return BUG_HINTS[normalized]
    if raw_hint and raw_hint.strip():
        return raw_hint.strip()
    return BUG_HINTS["default"]


def main() -> None:
    st.set_page_config(page_title="Code Teaching Assistant", layout="wide")
    st.title("Code Teaching Assistant")
    st.write("Select a problem, paste your Python solution, and check test diagnostics and targeted hints.")

    problem_keys = list(PROBLEM_SPECS.keys())
    selected_problem = st.selectbox(
        "Choose a problem",
        options=problem_keys,
        format_func=lambda key: PROBLEM_SPECS[key]["label"],
    )
    expected_func = PROBLEM_SPECS[selected_problem]["func_name"]

    default_snippet = f"def {expected_func}(x):\n    # Write your solution here\n    pass\n"
    student_code = st.text_area("Paste your Python code here", value=default_snippet, height=240)

    if st.button("Run & Get Hint", type="primary"):
        if not student_code.strip():
            st.warning("Please paste some Python code before running.")
            return

        func_exists, syntax_error = has_defined_function(student_code, expected_func)
        if syntax_error:
            st.error(syntax_error)
            st.info(BUG_HINTS["default"])
            return

        if not func_exists:
            st.warning("Your code doesn't define this function, did you pick the right problem?")
            return

        run_result = None
        if callable(run_code):
            try:
                run_result = run_code(student_code, selected_problem)
            except TypeError:
                run_result = run_code(student_code)

        bug_type = None
        line_no = None
        raw_hint = None

        if isinstance(run_result, dict):
            bug_type = run_result.get("bug_type") or run_result.get("error_type")
            line_no = run_result.get("line") or run_result.get("line_no")
            raw_hint = run_result.get("hint")
            st.write("**Test Output:**", run_result)
        elif run_result is not None:
            st.write("**Test Output:**", run_result)

        if callable(localize_bug) and line_no is None:
            try:
                loc = localize_bug(student_code, selected_problem)
                if isinstance(loc, dict):
                    line_no = loc.get("line") or loc.get("line_no")
                    bug_type = bug_type or loc.get("bug_type")
                elif isinstance(loc, int):
                    line_no = loc
            except Exception:
                pass

        if callable(try_repairs):
            try:
                repair_out = try_repairs(student_code, selected_problem)
                if isinstance(repair_out, dict):
                    bug_type = bug_type or repair_out.get("bug_type")
                    line_no = line_no or repair_out.get("line") or repair_out.get("line_no")
                    raw_hint = raw_hint or repair_out.get("hint")
            except Exception:
                pass

        if line_no is not None:
            st.info(f"**Estimated Line:** {line_no}")
        if bug_type:
            st.info(f"**Detected Bug Type:** {bug_type}")

        st.subheader("Hint")
        st.write(format_hint(bug_type, raw_hint))


if __name__ == "__main__":
    main()
