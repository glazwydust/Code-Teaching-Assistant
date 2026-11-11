import traceback
from runner import run_code

def is_successful(result):
    """Checks if all tests passed."""
    if isinstance(result, dict):
        num_passed = result.get("num_passed", 0)
        num_total = result.get("num_total", 0)
        if num_total > 0 and num_passed == num_total:
            return True
        if result.get("status") == "passed":
            return True
    return False

def try_repairs(code_string, suspicious_lines, predicted_bug_types, problem_id):
    """
    Attempts to repair the given code string, ensuring state resets between attempts.
    """
    # 1. Check if initial code already passes
    initial_result = run_code(code_string, problem_id)
    if is_successful(initial_result):
        return {
            "success": True,
            "message": "Code already passes all tests.",
            "repaired_code": code_string
        }

    # 2. Iterate through predicted bug types
    for bug_type in predicted_bug_types:
        if bug_type == "off_by_one":
            for line_no in suspicious_lines:
                # ALWAYS reset lines to a fresh copy of code_string for each try!
                lines = code_string.splitlines()
                idx = line_no - 1
                
                if 0 <= idx < len(lines):
                    target_line = lines[idx]
                    if "- 1" in target_line or "-1" in target_line:
                        # Apply fix
                        fixed_line = target_line.replace("- 1", "").replace("-1", "")
                        lines[idx] = fixed_line
                        
                        candidate_code = "\n".join(lines) + "\n"
                        
                        # Test candidate repair
                        test_result = run_code(candidate_code, problem_id)
                        if is_successful(test_result):
                            return {
                                "success": True,
                                "message": "Successfully repaired off-by-one bug.",
                                "repaired_code": candidate_code.strip()
                            }

        # TODO: Add handlers for other bug types here, or integrate AST logic from auto_label.py

    return {
        "success": False,
        "message": "Could not find a valid repair.",
        "repaired_code": code_string
    }
