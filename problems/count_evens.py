PROBLEM = {
    "id": "count_evens",
    "function": "count_evens",
    "title": "Count the even numbers",
    "description": (
        "Write a function count_evens(nums) that returns how many even numbers are in the list nums.\n"
        "Zero counts as even. An empty list returns 0."
    ),
    # Tricky cases: empty, zero, negatives, all odd, all even.
    "tests": [
        ([[]], 0),
        ([[0]], 1),
        ([[-2, -3, 4]], 2),
        ([[1, 3, 5]], 0),
        ([[2, 4, 6]], 3),
        ([[7]], 0),
        ([[-4]], 1),
    ],
    "solutions": [
        # 1. for-each with counter
        "def count_evens(nums):\n"
        "    count = 0\n"
        "    for x in nums:\n"
        "        if x % 2 == 0:\n"
        "            count += 1\n"
        "    return count\n",
        # 2. index loop
        "def count_evens(nums):\n"
        "    c = 0\n"
        "    for i in range(len(nums)):\n"
        "        if nums[i] % 2 == 0:\n"
        "            c = c + 1\n"
        "    return c\n",
        # 3. while loop
        "def count_evens(nums):\n"
        "    i = 0\n"
        "    total = 0\n"
        "    while i < len(nums):\n"
        "        if nums[i] % 2 == 0:\n"
        "            total += 1\n"
        "        i += 1\n"
        "    return total\n",
        # 4. comprehension
        "def count_evens(nums):\n"
        "    return len([x for x in nums if x % 2 == 0])\n",
    ],
    "buggy_examples": [
        {
            "name": "counts_odds",
            "bug_type": "wrong_operator",
            "code": "def count_evens(nums):\n"
                    "    count = 0\n"
                    "    for x in nums:\n"
                    "        if x % 2 == 1:\n"
                    "            count += 1\n"
                    "    return count\n",
            "bug_lines": [4],
        },
        {
            "name": "counter_starts_at_one",
            "bug_type": "wrong_init",
            "code": "def count_evens(nums):\n"
                    "    count = 1\n"
                    "    for x in nums:\n"
                    "        if x % 2 == 0:\n"
                    "            count += 1\n"
                    "    return count\n",
            "bug_lines": [2],
        },
    ],
}
