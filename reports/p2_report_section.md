# Bug-type classification (P2)

## What this part does

When a student's program fails, the rest of the system needs to know what kind of mistake it is before it can try a repair or write a hint. This component looks at the failing program and ranks eight bug types by how likely each one is: a missing edge case, a wrong operator, an off-by-one error, a missing return, a misspelt name, the wrong variable, a wrong starting value, and a return that fires too early inside a loop. A ninth type, a missing base case in recursion, is kept for completeness, but only one real program in the dataset has it. If the model is not confident, it says so, and the app falls back to a general hint instead of naming a bug type.

## Data

The NUS Refactory dataset gives us 2,442 correct and 1,783 wrong student programs across five introductory Python problems, but none of the wrong programs come with bug labels. We dealt with this in two ways.

**Training data, made on purpose.** We took correct programs and broke them in one known way at a time: deleting the empty-input check, moving the final return into the loop, misspelling a variable, and so on. A broken program was kept only if the tests now failed, and failed in the way its label claims. A `missing_edge_case` had to fail on an empty input, and an `early_return` had to fail on a non-empty one, which is the rule the team agreed on when defining that type. One thing surprised us: of the 2,467 correct programs, only 934 are actually different once variable names are ignored. The rest are near-copies. From those 934 we produced 6,392 labelled buggy programs, 205 of them with two bugs at once.

**Real test data, labelled by repair.** For each real wrong program, we tried small fixes, each tied to one bug type, and re-ran the tests. If a fix made every test pass, its bug type became the label, with the edit as evidence. This confirmed labels for 791 programs. Another 310 programs could not be explained by any small fix; these are usually a wrong approach rather than a single slip, and we label them `other`. Finally, a teammate labelled 50 real programs by hand. We never used those 50 for any design decision, so they serve as the final, independent test.

## Model

Each program is described in two ways. The first is what the code *looks like*. We rename every variable to a neutral token, so that `count` and `total` look the same, then count short sequences of tokens (TF-IDF over 1–3-token n-grams), alongside a few structural checks such as "is there a return inside a loop?" or "is a list item read before any empty check?". The second is what the code *does* on the tests: whether it crashed and with which error, whether it returned `None`, whether only the empty-input test failed, and whether a numeric answer was off by exactly one.

A separate logistic regression is trained for each bug type (one-vs-rest, with class weights balanced), so a program can carry more than one label. The regularisation strength was chosen by cross-validation on the generated data, split by the correct program each bug came from. Without that grouping, near-identical siblings land on both sides of the split and the scores are inflated.

A second, much smaller logistic regression decides whether to trust the first model's top guess. It looks at how far ahead the top bug type is and at how the program behaved, and it was trained on the auto-labelled real programs. Judged by cross-validation, it separates right from wrong guesses with an AUC of 0.89, compared with 0.66 for the top probability alone.

Only the functions in a submission are read by the model; example lines a student adds below their function (test calls, prints) are ignored when building these features, although the tests still run the whole program. The trained model is exported as a plain JSON file and evaluated in pure Python, so the app needs no machine-learning libraries and runs the same on any operating system. We checked that this copy matches the scikit-learn model to within 5 × 10⁻⁶, and that the test results the app gets from the runner produce the same features as those used in training (110 of 110 programs identical).

## Results

| Test set | Programs | Top guess correct |
|---|---|---|
| Generated bugs (5-fold CV, grouped by source program) | 6,392 | 87% |
| Real programs, auto-labelled | 768 | 81% |
| Real programs, hand-labelled (those with a named bug) | 41 | 63% |

For comparison, the keyword rules the app used before the model existed got the top guess right 25% of the time on the auto-labelled set and 22% on the hand-labelled set. Always guessing the most common bug type scores 4% and 17%.

The drop from generated to real bugs is the most informative number here. It is largest for `off_by_one` (73% recall on generated bugs, 48% on real ones) and `wrong_variable` (68% to 53%). These are the types where real students' mistakes look least like the clean single edits we generate. Bugs with a strong behavioural signature hold up well: a misspelt name causes a `NameError`, a missing return produces `None`, and an early return usually stops after the first item. All three stay at 90% recall or above on real code.

Both kinds of evidence matter. Trained on the code text alone, the model gets 51% of the hand-labelled programs right. On test behaviour and structure alone, it gets 56%. Combined, it gets 63%.

**Admitting uncertainty.** With the confidence cut-off chosen on the auto-labelled programs, the model answers 58% of them and is right on 85% of the answers it gives. On the 327 `other` programs, the ones it cannot meaningfully diagnose, it says "not sure" 292 times. On the hand-labelled set, it answered 18 of the 50 programs, got all 18 right, and said "not sure" on all 9 that the labeller had marked `other`. The set is small, so the 18-of-18 result should be read as encouraging rather than as a precise accuracy figure.

## Limitations

- **The auto-labelled score is optimistic.** Those labels come from the same kind of small edits used to generate the training bugs, which favours the model. The hand-labelled set is the fairer test, but at 50 programs each one moves the percentages by two to three points.
- **New problems are hard.** When one NUS problem is held out entirely and the model is trained on the other four, accuracy on real programs for the held-out problem ranges from 35% (problem 1) to 91% (problem 2). Problem 1's real bugs are dominated by empty-input crashes, and that pattern looks different in the other problems. A deployment on new exercises should expect results closer to this range than to the in-distribution numbers.
- **Some test programs have twins in the training data.** 53 of the validation programs and 4 of the hand-labelled ones are identical, after renaming, to a generated training program. Removing them changes the auto-labelled score by about one point.
- **Two bug types are too rare to evaluate.** `missing_base_case` and `wrong_init` appear too rarely in the real data to say much about them.
