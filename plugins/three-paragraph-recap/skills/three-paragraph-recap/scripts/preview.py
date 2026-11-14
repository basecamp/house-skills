#!/usr/bin/env python3
"""Check a draft against the contract before it is posted.

  preview.py <draft.html>              a card comment or message
  preview.py <draft.html> --chat       a chat line or DM, tighter word budget

Exit status is 0 when it passes and 1 when it does not, so this drops into a
pre-commit hook, a CI step, or a PreToolUse hook without parsing its output.

The draft is the HTML body you would post: `<p>` paragraphs, plus tables, images
and figures where they earn their place. Write it to a file rather than passing
it as an argument -- prose is full of apostrophes and quoting it through a shell
is how the checked bytes and the posted bytes stop being the same bytes.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract

# A chat message is read in a notification with everything else around it, not on
# the record it is about. Same three paragraphs, half the words.
CHAT_PARA_WORDS = 45


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    chat = "--chat" in sys.argv

    if len(args) != 1:
        print(__doc__.strip())
        return 2

    path = args[0]
    if not os.path.exists(path):
        print(f"no such file: {path}")
        return 2

    html = open(path).read().strip()
    if not html:
        print("the draft is empty")
        return 2

    paras = contract.paragraphs(html)
    if not paras:
        print("the draft has no body")
        return 2

    caps = {"max_para_words": CHAT_PARA_WORDS,
            "max_total_words": 3 * CHAT_PARA_WORDS} if chat else {}
    problems = contract.contract_problems(paras, **caps)

    if not problems:
        print("passes")
        return 0

    print(f"{len(problems)} problem(s):")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
