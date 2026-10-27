PROBLEM = {
    "id": "factorial",
    "function": "factorial",
    "title": "Factorial",
    "description": (
        "Write a function factorial(n) that returns n! for an integer n >= 0.\n"
        "factorial(0) is 1. Do not use math.factorial."
    ),
    # Tricky cases: 0 and 1 (base cases), small values, a bigger value.
    "tests": [
        ([0], 1),
        ([1], 1),
        ([2], 2),
        ([3], 6),
        ([5], 120),
        ([7], 5040),
        ([10], 3628800),
    ],
    "solutions": [
        # 1. for loop
        "def factorial(n):\n"
        "    result = 1\n"
        "    for i in range(2, n + 1):\n"
        "        result *= i\n"
        "    return result\n",
        # 2. while loop counting down
        "def factorial(n):\n"
        "    result = 1\n"
        "    while n > 1:\n"
        "        result *= n\n"
        "        n -= 1\n"
        "    return result\n",
        # 3. recursion
        "def factorial(n):\n"
        "    if n <= 1:\n"
        "        return 1\n"
        "    return n * factorial(n - 1)\n",
        # 4. reduce
        "from functools import reduce\n"
        "\n"
        "def factorial(n):\n"
        "    return reduce(lambda a, b: a * b, range(1, n + 1), 1)\n",
    ],
    "buggy_examples": [
        {
            "name": "base_case_skips_zero",
            "bug_type": "missing_base_case",
            "code": "def factorial(n):\n"
                    "    if n == 1:\n"
                    "        return 1\n"
                    "    return n * factorial(n - 1)\n",
            "bug_lines": [2],
        },
        {
            "name": "product_starts_at_zero",
            "bug_type": "wrong_init",
            "code": "def factorial(n):\n"
                    "    result = 0\n"
                    "    for i in range(1, n + 1):\n"
                    "        result *= i\n"
                    "    return result\n",
            "bug_lines": [2],
        },
    ],
}
