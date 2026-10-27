PROBLEM = {
    "id": "sum_list",
    "function": "sum_list",
    "title": "Sum of a list",
    "description": (
        "Write a function sum_list(nums) that returns the sum of all integers in the list nums.\n"
        "Do not use the built-in sum(). An empty list returns 0."
    ),
    # Tricky cases: empty, single item, negatives, zeros, values that cancel out.
    "tests": [
        ([[]], 0),
        ([[5]], 5),
        ([[1, 2, 3]], 6),
        ([[-1, -2, 3]], 0),
        ([[0, 0]], 0),
        ([[10, -10, 10]], 10),
        ([[100]], 100),
    ],
    "solutions": [
        # 1. for-each loop
        "def sum_list(nums):\n"
        "    total = 0\n"
        "    for x in nums:\n"
        "        total += x\n"
        "    return total\n",
        # 2. index loop
        "def sum_list(nums):\n"
        "    s = 0\n"
        "    for i in range(len(nums)):\n"
        "        s = s + nums[i]\n"
        "    return s\n",
        # 3. while loop
        "def sum_list(nums):\n"
        "    i = 0\n"
        "    result = 0\n"
        "    while i < len(nums):\n"
        "        result += nums[i]\n"
        "        i += 1\n"
        "    return result\n",
        # 4. recursion
        "def sum_list(nums):\n"
        "    if not nums:\n"
        "        return 0\n"
        "    return nums[0] + sum_list(nums[1:])\n",
    ],
    "buggy_examples": [
        {
            "name": "loop_stops_early",
            "bug_type": "off_by_one",
            "code": "def sum_list(nums):\n"
                    "    s = 0\n"
                    "    for i in range(len(nums) - 1):\n"
                    "        s += nums[i]\n"
                    "    return s\n",
            "bug_lines": [3],
        },
        {
            "name": "starts_at_one",
            "bug_type": "wrong_init",
            "code": "def sum_list(nums):\n"
                    "    s = 1\n"
                    "    for x in nums:\n"
                    "        s += x\n"
                    "    return s\n",
            "bug_lines": [2],
        },
    ],
}
