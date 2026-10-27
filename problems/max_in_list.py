PROBLEM = {
    "id": "max_in_list",
    "function": "max_in_list",
    "title": "Largest number in a list",
    "description": (
        "Write a function max_in_list(nums) that returns the largest number in a non-empty list.\n"
        "Do not use the built-in max()."
    ),
    # Tricky cases: single item, all negative, ties, max first vs max last.
    "tests": [
        ([[7]], 7),
        ([[-3, -1, -2]], -1),
        ([[1, 5, 5, 2]], 5),
        ([[9, 2]], 9),
        ([[2, 9]], 9),
        ([[-5]], -5),
        ([[3, 3, 3]], 3),
        ([[0, -1]], 0),
    ],
    "solutions": [
        # 1. for-each, start from first item
        "def max_in_list(nums):\n"
        "    best = nums[0]\n"
        "    for x in nums:\n"
        "        if x > best:\n"
        "            best = x\n"
        "    return best\n",
        # 2. index loop, skip the first item
        "def max_in_list(nums):\n"
        "    best = nums[0]\n"
        "    for i in range(1, len(nums)):\n"
        "        if nums[i] > best:\n"
        "            best = nums[i]\n"
        "    return best\n",
        # 3. while loop
        "def max_in_list(nums):\n"
        "    i = 1\n"
        "    best = nums[0]\n"
        "    while i < len(nums):\n"
        "        if nums[i] > best:\n"
        "            best = nums[i]\n"
        "        i += 1\n"
        "    return best\n",
        # 4. recursion
        "def max_in_list(nums):\n"
        "    if len(nums) == 1:\n"
        "        return nums[0]\n"
        "    rest = max_in_list(nums[1:])\n"
        "    return nums[0] if nums[0] > rest else rest\n",
    ],
    "buggy_examples": [
        {
            "name": "starts_at_zero",
            "bug_type": "wrong_init",
            "code": "def max_in_list(nums):\n"
                    "    best = 0\n"
                    "    for x in nums:\n"
                    "        if x > best:\n"
                    "            best = x\n"
                    "    return best\n",
            "bug_lines": [2],
        },
        {
            "name": "finds_smallest",
            "bug_type": "wrong_operator",
            "code": "def max_in_list(nums):\n"
                    "    best = nums[0]\n"
                    "    for x in nums:\n"
                    "        if x < best:\n"
                    "            best = x\n"
                    "    return best\n",
            "bug_lines": [4],
        },
    ],
}
