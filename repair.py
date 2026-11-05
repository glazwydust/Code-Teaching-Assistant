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
    Attempts to repair the given code string based on suspicious lines and bug types.
    """
    lines = code_string.splitlines()

    if "off_by_one" in predicted_bug_types:
        for i, line in enumerate(lines):
            if "- 1" in line or "-1" in line:
                fixed_line = line.replace("- 1", "").replace("-1", "")
                lines[i] = fixed_line
                
                candidate_code = "\n".join(lines) + "\n"
                
                test_result = run_code(candidate_code, problem_id)
                print(f"DEBUG REPAIR - Candidate test_result: {test_result}")
                
                if is_successful(test_result):
                    return {
                        "success": True,
                        "message": "Successfully repaired off-by-one bug.",
                        "repaired_code": candidate_code.strip()
                    }

    return {
        "success": False,
        "message": "Could not find a valid repair.",
        "repaired_code": code_string
    }
