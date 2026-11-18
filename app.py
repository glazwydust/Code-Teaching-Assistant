import streamlit as st
from runner import run_code
from repair import try_repairs
from classifier import predict_bugs   # P2: trained bug-type model
from localize import score_lines       # P1: suspicious lines from the same test run

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
            # trace=True also records which lines ran, so one run serves P1, P2 and P3
            test_result = run_code(student_code, problem_choice, trace=True)
            
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
                    # 2. Bug type from P2's trained model, suspicious lines from P1's localizer
                    prediction = predict_bugs(student_code, problem_choice, run_result=test_result)
                    ranked_bugs = [bug for bug, _ in prediction["bugs"]]
                    not_sure = prediction["not_sure"] or not ranked_bugs
                    detected_bug = "not_sure" if not_sure else ranked_bugs[0]
                    line_scores = score_lines(test_result.get("tests", []))
                    suspicious_lines = [d["line"] for d in line_scores[:3]] or [1]
                    suspicious_line = suspicious_lines[0]

                    # 3. Call P3 repair engine (it tries the most likely bug types first)
                    repair_output = try_repairs(student_code, suspicious_lines, ranked_bugs[:3], problem_choice)

                    
                    # 4. Craft safe, non-leaking pedagogical hints
                    hint_库 = {
                        "wrong_init": f"Check the starting value of your variables around line {suspicious_line}. What should they hold before the loop begins?",
                        "missing_edge_case": "Your code fails on an edge case. What happens when the input is empty? Trace your code by hand with an empty list.",
                        "missing_return": "Look at what your function gives back. Does every path end with a `return`, and does it return the result rather than printing it?",
                        "name_typo": f"Python can't find one of the names you use around line {suspicious_line}. Compare its spelling with where you created it.",
                        "wrong_variable": f"Around line {suspicious_line}, check that each variable is the one you mean. Is one of them standing in for another?",
                        "early_return": "Your function may be returning too soon. Does it really check every item before it decides?",
                        "missing_base_case": "Your recursive function needs a stopping point. When should it return without calling itself again?",
                        "not_sure": f"Your code failed {total - passed} test(s). Pick one failing test and trace your code line by line, starting around line {suspicious_line}.",
                        "off_by_one": f"Your code failed {total - passed} test(s). Check your loop bounds or index ranges around line {suspicious_line}—it might be stopping prematurely or missing boundary conditions.",
                        "wrong_operator": f"Your code failed {total - passed} test(s). Review the operators used around line {suspicious_line}."
                    }
                    
                    targeted_hint = hint_库.get(detected_bug, f"Review your logic and conditions around line {suspicious_line} where the test outputs deviate.")
                    
                    if repair_output and repair_output.get("success"):
                        targeted_hint = repair_output.get("message", targeted_hint)

                    # Display clean diagnostics and hints
                    if not_sure:
                        st.warning(f"🔍 **Detected Issue Type:** not sure (the model is not confident). Look near line `{suspicious_line}`")
                    else:
                        st.warning(f"🔍 **Detected Issue Type:** `{detected_bug}` (Estimated near line `{suspicious_line}`)")
                    st.info(f"💡 **Targeted Hint:** {targeted_hint}")