import streamlit as st
from runner import run_code
from repair import try_repairs

# Page Configuration
st.set_page_config(page_title="AI Coding Teaching Assistant", page_icon="🤖", layout="centered")

st.title("🤖 AI Coding Teaching Assistant")
st.markdown("Paste your Python code below to get automated diagnostics, test breakdowns, and targeted hints.")

# Problem selection matching the NUS dataset tasks
problem_choice = st.selectbox(
    "Select Problem", 
    ["factorial", "is_palindrome", "count_evens", "max_in_list", "sum_list"]
)

# Text area for student code input
student_code = st.text_area("Paste your Python code here:", height=220, placeholder="def is_palindrome(s):\n    ...")

if st.button("Get Hint & Diagnostics", type="primary"):
    if not student_code.strip():
        st.warning("⚠️ Please enter or paste some Python code first.")
    else:
        with st.spinner("Running test cases and analyzing your logic..."):
            
            # 1. Execute code against test suites using P1's runner
            test_result = run_code(student_code, problem_choice)
            
            st.markdown("---")
            st.subheader("📊 Diagnostic Results")
            st.markdown(f"**Target Problem:** `{problem_choice}`")
            
            if isinstance(test_result, dict):
                status = test_result.get('status', 'unknown')
                passed = test_result.get('num_passed', 0)
                total = test_result.get('num_total', 0)
                
                st.markdown(f"**Execution Status:** `{status.capitalize()}`")
                st.markdown(f"**Test Cases Passed:** `{passed} / {total}`")
                
                if passed == total and total > 0:
                    st.success("🎉 Excellent! Your code passed all test cases successfully.")
                else:
                    # 2. Smart Code Analysis & Bug Classification
                    detected_bug = "off_by_one"
                    suspicious_line = 1
                    
                    code_lines = student_code.splitlines()
                    for idx, line in enumerate(code_lines, 1):
                        # Detect palindrome string assignment errors (e.g., reverse = s)
                        if problem_choice == "is_palindrome" and ("reverse = s" in line or "rev = s" in line):
                            detected_bug = "wrong_starting_value"
                            suspicious_line = idx
                            break
                        # Detect loop bounds or range issues
                        elif "range" in line:
                            detected_bug = "off_by_one"
                            suspicious_line = idx
                            break
                        # Detect operator issues
                        elif "=" in line and ("==" not in line) and ("!=" not in line) and any(op in line for op in ['+', '-', '*', '/']):
                            detected_bug = "wrong_operator"
                            suspicious_line = idx
                            break

                    # 3. Call P3 repair engine or use smart pedagogical fallbacks
                    repair_output = try_repairs(student_code, [suspicious_line], [detected_bug], problem_choice)
                    
                    # 4. Craft safe, non-leaking pedagogical hints
                    hint_库 = {
                        "wrong_starting_value": f"Check your variable initialization or assignment around line {suspicious_line}. Are you properly transforming or reversing the sequence, or just copying it?",
                        "off_by_one": f"Your code failed {total - passed} test(s). Check your loop bounds or index ranges around line {suspicious_line}—it might be stopping prematurely or missing boundary conditions.",
                        "wrong_operator": f"Your code failed {total - passed} test(s). Review the operators used around line {suspicious_line}."
                    }
                    
                    targeted_hint = hint_库.get(detected_bug, f"Review your logic and conditions around line {suspicious_line} where the test outputs deviate.")
                    
                    if repair_output and repair_output.get("success"):
                        targeted_hint = repair_output.get("message", targeted_hint)

                    # Display clean diagnostics and hints
                    st.warning(f"🔍 **Detected Issue Type:** `{detected_bug}` (Estimated near line `{suspicious_line}`)")
                    st.info(f"💡 **Targeted Hint:** {targeted_hint}")