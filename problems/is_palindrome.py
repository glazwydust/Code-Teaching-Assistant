PROBLEM = {
    "id": "is_palindrome",
    "function": "is_palindrome",
    "title": "Palindrome check",
    "description": (
        "Write a function is_palindrome(s) that returns True if the string s reads the same "
        "forwards and backwards, otherwise False.\n"
        "The check is case-sensitive and spaces count as characters. The empty string is a palindrome."
    ),
    # Tricky cases: empty, 1 char, even/odd length, near-miss, case, spaces.
    "tests": [
        ([""], True),
        (["a"], True),
        (["aa"], True),
        (["ab"], False),
        (["abba"], True),
        (["racecar"], True),
        (["abca"], False),
        (["Aa"], False),
        (["ab ba"], True),
        (["abcd"], False),
    ],
    "solutions": [
        # 1. slicing
        "def is_palindrome(s):\n"
        "    return s == s[::-1]\n",
        # 2. two pointers
        "def is_palindrome(s):\n"
        "    i, j = 0, len(s) - 1\n"
        "    while i < j:\n"
        "        if s[i] != s[j]:\n"
        "            return False\n"
        "        i += 1\n"
        "        j -= 1\n"
        "    return True\n",
        # 3. loop over first half
        "def is_palindrome(s):\n"
        "    n = len(s)\n"
        "    for i in range(n // 2):\n"
        "        if s[i] != s[n - 1 - i]:\n"
        "            return False\n"
        "    return True\n",
        # 4. recursion
        "def is_palindrome(s):\n"
        "    if len(s) < 2:\n"
        "        return True\n"
        "    if s[0] != s[-1]:\n"
        "        return False\n"
        "    return is_palindrome(s[1:-1])\n",
    ],
    "buggy_examples": [
        {
            "name": "forgot_final_return",
            "bug_type": "missing_return",
            # The bug is a missing `return True` after the loop; we point at the last line.
            "code": "def is_palindrome(s):\n"
                    "    i, j = 0, len(s) - 1\n"
                    "    while i < j:\n"
                    "        if s[i] != s[j]:\n"
                    "            return False\n"
                    "        i += 1\n"
                    "        j -= 1\n",
            "bug_lines": [7],
        },
        {
            "name": "checks_too_few_pairs",
            "bug_type": "off_by_one",
            "code": "def is_palindrome(s):\n"
                    "    for i in range(len(s) // 2 - 1):\n"
                    "        if s[i] != s[len(s) - 1 - i]:\n"
                    "            return False\n"
                    "    return True\n",
            "bug_lines": [2],
        },
    ],
}
