#!/usr/bin/env python3
"""Each case is a draft and the rule it must trip. Run before changing a rule.

These are the failures the rules were written for, kept as the record of what
each one is actually catching. A rule that stops firing on its own case has been
broken by an edit somewhere else — which is the whole reason this file exists,
because the patterns interact more than they look like they do.

  python3 test_contract.py
"""
import sys

import contract

CLEAN = [
    "<p>The desktop app drops link annotations when it prints to PDF, so a printed doc arrives with dead links.</p>",
    "<p>The cause sits in the print path, which rasterises the page before the annotations attach to it.</p>",
    "<p>Next step: you decide whether this ships in 5.0.4 or waits for the next minor.</p>",
]


def draft(*paragraphs):
    return [f"<p>{p}</p>" for p in paragraphs]


# (name, draft, substring that must appear in some problem)
CASES = [
    ("soft ask",
     draft("The upload path drops files above two megabytes without telling the sender.",
           "Let me know if you want me to fix it.", "Next step: you decide."),
     "soft-ask"),
    ("emoji",
     draft("The upload path drops large files \U0001F389.", "It is silent.",
           "Next step: you decide."),
     "emoji"),
    ("bullet list",
     ["<p>It broke.</p>", "<ul><li>one</li></ul>", "<p>Next step: you decide.</p>"],
     "bullet list"),
    ("metaphor",
     draft("The bridge goes quiet when the destination detaches.", "Nothing reports it.",
           "Next step: you decide."),
     "metaphor"),
    ("hedge",
     draft("The upload path somewhat drops large files over two megabytes.",
           "It is not reported.", "Next step: you decide."),
     "hedge"),
    ("em dash",
     draft("The upload path drops large files — silently.", "Nothing reports it.",
           "Next step: you decide."),
     "dash"),
    ("no next step",
     draft("The upload path drops large files.", "Nothing reports it.", "It is done."),
     "next step"),
    ("four paragraphs",
     draft("One thing broke here.", "Two things broke here.", "Three broke here.",
           "Next step: you decide."),
     "prose paragraphs"),
    ("counts your own work",
     draft("The fix touches ten files and adds 1246 tests.", "It is ready.",
           "Next step: you decide."),
     "counts your own work"),
    ("minutes",
     draft("The fix took 45 minutes to land.", "It is ready.", "Next step: you decide."),
     "minutes"),
    ("sales word",
     draft("The new upload path is a robust improvement.", "It is ready.",
           "Next step: you decide."),
     "sales word"),
    ("bookkeeping",
     draft("The finding is recorded in the tracker under last week's batch.",
           "It is ready.", "Next step: you decide."),
     "bookkeeping"),
    ("process status",
     draft("The build is green.", "Users are unaffected.", "Next step: you decide."),
     "process status"),
    ("counted but unnamed",
     draft("The uploader change leaves the three issues open and blocking release.",
           "Support has seen all of them.", "Next step: you decide."),
     "cannot see"),
    # A Basecamp link whose anchor text is the URL. Deliberately not a GitHub PR
    # link: that check shells out to `gh`, is opt-in, and a test must not need a
    # network.
    ("link shows its URL",
     ["<p>The report is at <a href=\"https://app.basecamp.com/1/buckets/2/messages/3\">"
      "https://app.basecamp.com/1/buckets/2/messages/3</a> for the counts.</p>",
      "<p>Support has confirmed it.</p>", "<p>Next step: you decide.</p>"],
     "shows its URL"),
    ("too many negations",
     draft("The bridge does not report the failure, no message reaches anyone, and nobody learns it stopped.",
           "It is silent.", "Next step: you decide."),
     "negates"),
    ("sentence too long",
     draft(" ".join(f"word{i}" for i in range(40)) + ".", "It is ready.",
           "Next step: you decide."),
     "cap is"),
]


def main():
    failures = []

    problems = contract.contract_problems(CLEAN)
    if problems:
        failures.append(("a clean draft passes", problems))

    for name, body, expected in CASES:
        problems = contract.contract_problems(body)
        if not any(expected in p for p in problems):
            failures.append((name, problems))

    # The chat budget has to actually bite where the card budget does not.
    wide = draft(" ".join(f"word{i}" for i in range(60)) + ".",
                 "A short second paragraph.", "Next step: you decide.")
    card = [p for p in contract.contract_problems(wide) if "cap is 90" in p]
    chat = [p for p in contract.contract_problems(wide, max_para_words=45,
                                                  max_total_words=135) if "cap is 45" in p]
    if card or not chat:
        failures.append(("chat mode tightens the per-paragraph cap", card + chat))

    for name, problems in failures:
        print(f"FAIL  {name}")
        for p in problems:
            print(f"        {p[:100]}")

    print(f"\n{len(CASES) + 2 - len(failures)}/{len(CASES) + 2} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
