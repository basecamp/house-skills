"""The contract: what a comment written for a reader may and may not do.

Extracted from a guard that has been refusing real comments on a real Basecamp
board since August 2026. Every rule here was added because something reached a
reader and should not have; the comment above each one says what that was. None
of them is a style preference somebody liked the sound of.

The audience is anyone running an agent that posts to Basecamp. That is why the
anti-machine rules are the bulk of it -- bookkeeping, process narration, proof of
work, counted-but-unnamed, ornament, hedging. Those are what generated prose does
wrong, reliably, and no amount of prompting fixes them for long. A gate does.

`contract_problems(paragraphs)` returns a list of strings, one per violation,
each naming the rule and what to do instead. Empty means it passes.

The caps are arguments because a chat message is a tighter surface than a card:
same shape, fewer words. Everything else is identical on both.
"""
import json
import os
import re
import subprocess
import time

# What counts as a block. A comment is HTML, so its paragraphs are its own tags,
# and a table or figure is a block that is deliberately NOT prose -- see NONPROSE
# below, which is what exempts them from the caps.
PARAGRAPH = re.compile(r"<p\b[^>]*>.*?</p>|<(?:ul|ol|table|figure|blockquote)\b.*?"
                       r"</(?:ul|ol|table|figure|blockquote)>", re.I | re.S)


# A body with no tags at all is one paragraph: that is what a one-line answer
# looks like, and it is a legitimate shape.
def paragraphs(html):
    blocks = [b for b in PARAGRAPH.findall(html)
              if re.sub(r"<[^>]+>|&nbsp;|\s", "", b)]
    if blocks:
        return blocks
    stripped = html.strip()
    return [stripped] if stripped else []


BANNED = [
    "say the word", "let me know", "feel free", "happy to", "if you'd like",
    "if you would like", "just let me", "would you like", "shall i",
    "want me to", "hope this helps", "i can also", "does that work",
    "sound good", "no worries", "great question", "fair challenge",
    "at the end of the day", "it is worth noting", "it's worth noting",
    "i'd be happy", "i would be happy", "don't hesitate", "do not hesitate",
    "please note", "reach out if",
]
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF️]")
NEXT_STEP = re.compile(r"\bnext step", re.I)

# Bookkeeping: sentences reporting where something was written down. The human
# card is a decision surface, not a ledger of our own process.
LEDGER = [
    (r"\brecorded (on|in|as|under)\b", "reports where something was recorded"),
    (r"\blogged (as|in|to|under)\b", "reports a bookkeeping write"),
    (r"\bI (have |just )?(updated|noted|appended|logged|recorded|filed)\b",
     "narrates yyour own bookkeeping"),
    (r"\bis (now )?(on|in) the (notes|tracker|ticket|card)\b",
     "reports where text was posted"),
    (r"\b(nobody|no one) has (yet )?(designed|planned|decided|written|sized)\b",
     "narrates which stage owes the work"),
    (r"\bhas (not )?been (designed|reviewed|sized|planned)\b",
     "reports an artifact's stage"),
    (r"\bgoes through (review|design|the battery)\b", "narrates your pipeline"),
    (r"\byou get back\b", "narrates what the process will hand over"),
]

# Effort accounting. The LEDGER patterns catch where a finding was FILED. They do
# not catch what the work COST you - minutes, estimate error, finding counts, a
# phase standing as the subject of a sentence. That is our bookkeeping too, and
# it belongs in your own notes. If the cost changes what the reader should decide, put the
# decision in front of him instead of the arithmetic behind it.
# A second exemption: a comment Fernando asked for as a TABLE. The prose caps -
# three paragraphs, sentence and word limits, one-fact-per-sentence - describe a
# decision surface written in sentences. A table he requested is data he intends
# to act from row by row, and counting its cells as sentences would refuse the
# thing he asked for. The tone rules still apply to any prose around it.
# Fernando, 2026-08-21: "the 3 paragraph rule stays, but adding images or tables
# or bullet-points for additional explanation is allowed."
#
# So a block that is a table, a list, an image or an attachment is NOT prose. It
# does not count toward the three, and no length cap applies to it - a table row
# is not a sentence and counting its cells as words refuses the thing he asked
# for. The three prose paragraphs still have to be there and still have to obey
# every cap. The tone rules apply to everything, extras included: a metaphor
# inside a bullet is still a metaphor.
# A mention expands to <bc-attachment><figure><img>…</figure></bc-attachment> and
# sits INSIDE the opening sentence. Classifying that paragraph as a picture drops
# the prose count from 3 to 2 and denies the comment: 121 of 143 real comments
# carry a mention and 120 of their denials had no other cause. Strip mentions
# before deciding what a paragraph is.
MENTION = re.compile(r"<bc-attachment\b[^>]*\bcontent-type=[\"']application/vnd\.basecamp\.mention"
                     r"[^>]*>.*?</bc-attachment>", re.I | re.S)
NONPROSE = re.compile(r"<(?:table|tr|td|th|figure|img|bc-attachment)\b", re.I)

# Bullet lists are gone. Fernando, on the voice: "Let's get rid of bullet points
# in the voice. They don't serve other purpose than to extend comments." He is
# describing what they were being used for -- a list is exempt from the word and
# sentence caps, so five bullets carry what three paragraphs are not allowed to
# say. Tables and images stay; they carry things prose genuinely cannot.
LISTS = re.compile(r"<(?:ul|ol|li)\b", re.I)

# Build artifacts, the same fault as the minutes below. The patterns here count
# what a phase COST in time; they never counted what the work TOUCHED, and
# Fernando named that on 2026-08-24: "there's no need to state files changed,
# number of tests passed." He called the passage "mostly useless" rather than
# useless, and the word is doing real work - inside the same paragraph
# "Missing those would have left Xcode pointed at 4.2.1" is a consequence he can
# act on. So this matches the COUNT, not the sentence carrying it: a sentence
# that states a consequence and happens to sit beside an inventory keeps its
# place, and only a body that actually counts our work is refused.
#
# The discriminator is the noun, never the number. "353 events and 126 users",
# "1.3.6 carries 654 users against 61 on 1.3.3" and "five events and three users
# sit under 100" measure the world and stand. "ten files", "1246 tests" and "Two
# commits" measure your own labour and do not.
#
# Three of the nouns he listed are deliberately absent, each for a measured
# reason (65 comments, the on-call cards, 22-25 August):
#
#   `lines` - fourteen sentences carry a line count and ten of them are
#   FORECASTS of work not yet done: "about seventy-five lines", "the tag is three
#   lines", "about fifty lines across three files". That number is the answer to
#   the question he is being asked - is this worth building - and refusing it
#   deletes the decision, not the bookkeeping. Hedges do not separate the two
#   ("The code fix is five lines" carries none), and keying on "about" would only
#   teach the bot to write it.
#
#   `runs` - ten of its twelve appearances are the verb ("the same write still
#   runs on the thread that draws the screen"), and the noun's one real use was
#   "two runs at once make the loser report skipped", a concurrency defect rather
#   than a count of our test passes.
#
#   `rows` - never once appears as a count in the corpus, while LEDGER already
#   refuses the ledger sense, and tables are welcome on this card by his own
#   ruling, so "five rows" is as likely to describe one of those.
NUMERAL = (r"\d[\d,]*|"
           r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
           r"(?:-(?:one|two|three|four|five|six|seven|eight|nine))?|"
           r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
           r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
           r"a dozen|dozens")
ARTIFACT = r"\w*files?|tests?|assertions?|failures?|insertions?|deletions?|commits?"
# `(?<![.\d])` keeps a version out of the count: these cards are thick with
# dotted versions AND with artifact nouns, and "the 1.34.13 tests" would
# otherwise read as thirteen of them. It is precautionary - it saves nothing in
# the 65-comment corpus, because the one sentence that needed it ("the 1.34.10
# comparison runs on identical capture settings") stopped matching when `runs`
# was dropped. Kept because the collision is one keystroke away, not because it
# was measured to fire.
#
# One adjective may sit between the count and the noun, but never a plural noun,
# which is what stops "a quarter's 1,252 events file as one issue" from reading
# as a file count. And a commit count that is a DISTANCE is a fact about the
# world, not about our labour: "seven commits behind" measures how far a pin
# lags its branch, which is true whether or not we ever touch it, so it stands
# for the same reason "654 users" does.
COUNTS_OUR_WORK = (
    r"(?<![.\d])\b(?:" + NUMERAL + r")\s+"
    r"(?:[a-z][a-z-]*(?<!s)\s+)?"
    r"(?:" + ARTIFACT + r")\b(?!\s+(?:behind|ahead|back)\b)")

ACCOUNTING = [
    (r"\b\d+\s*(?:minutes?|mins?|hours?)\b", "counts your minutes"),
    (COUNTS_OUR_WORK, "counts your own work - files touched, tests run, commits made"),
    (r"\b(?:" + NUMERAL + r")\s+(?:findings?|blockers?|defects?|errata|nits?)\b",
     "counts your findings"),
    (r"\b(?:overrun|under-?ran|est\.|LOC)\b", "estimate vocabulary"),
    (r"\b(?:planning|design|intake|review|testing|postmortem|the battery)\s+"
     r"(?:cost|took|priced|produced|returned|found|caught|ran)\b",
     "a phase of ours acting as the subject"),
    (r"\bthe effort (?:now )?(?:reads|stands|costs|runs)\b", "narrates effort accounting"),
]

# Internal vocabulary. Terms that only parse if the reader has your working notes open.
JARGON = [
    (r"\bitems?\s+\d+\b", "numbered internal item"),
    (r"\bUNVERIFIED\b", "internal tracking label"),
    (r"\bverdicts?\b", "internal term"),
    (r"\bretired by\b", "internal status language"),
    (r"\bunconditional(ly)?\b", "internal status language"),
    (r"\bconditional on\b", "internal status language"),
    (r"\bdesign(ed)? intent\b", "internal term"),
    (r"\bacceptance (row|rows|ledger|criteria)\b", "internal artefact"),
    (r"\bstep \d+ of\b", "cites an internal step number"),
    (r"\b(the )?(causal )?chain\b", "internal term for the mechanism"),
    (r"\brivals?\b", "internal term"),
    (r"\bshelf survey\b", "internal artefact"),
]
# Voice. A claim about a mechanism has to say what the mechanism DOES. "The check
# cannot fail" reads two opposite ways - the check is inert, or the check must not
# fail - and the reader cannot tell which from the sentence. Name the actor and the
# action instead. Kept deliberately narrow: broad passive-voice detection fires on
# ordinary English, and an over-broad pattern costs more than the fault it catches.
VOICE = [
    (r"\b(?:can ?not|can't|could ?n[o']t|will not|won't|does not|doesn't|do not|"
     r"don't|never)\s+(?:fails?|be trusted|be relied on|catch(?:es)? anything|rejects?)\b",
     "says what it will not do instead of what it does",
     "Say the action: 'passes whatever the fuses read', 'reports every body as clean'."),
    (r"\b(?:is|are|was|were)\s+(?:not\s+)?"
     r"(?:verified|enforced|guarded|checked|covered|asserted|validated)\b(?!\s+by\b)",
     "agentless passive - nothing in the sentence does the verifying",
     "Name who or what does it: 'the workflow checks X', 'no test asserts X'."),
    (r"\bnothing\s+(?:is|was)\s+(?:done|changed|checked)\b",
     "agentless passive with no actor",
     "Name the actor and the action."),
    (r"\bthe (?:gap|thing|part|piece|area|place|bit|spot) to "
     r"(?:look at|watch|check|fix|examine|dig into)\b",
     "an action packed into a noun phrase, with nobody doing it",
     "Say who does what: 'we need to look upstream', not 'the gap to look at sits upstream'."),
]
# Tone. Two habits, both of them the writer showing up in a report that should
# only carry the finding.
#
# EMPHASIS: intensifiers and absolutes arranged for effect. A fact does not need
# "exactly" or "at all" to land, and a paragraph built to a reveal makes the
# reader wait for information they could have had in the first clause.
#
# EDITORIAL: sentences that judge rather than report - scoring the finding
# instead of stating it. Whether a thing was the right call is the reader's to
# decide from the facts.
EMPHASIS = [
    r"\bexactly\b", r"\bprecisely\b", r"\bat all\b", r"\boutright\b",
    r"\bthe single (?:one|thing|place|workflow|test|file)\b", r"\bthe very\b",
    r"\band nowhere else\b", r"\bnothing but\b", r"\bnot one\b",
    r"\bsimply\b", r"\bmerely\b", r"\bof course\b", r"\bobviously\b",
    r"\bentire(?:ly)?\b", r"\bwhatsoever\b", r"\bflatly\b",
]
EDITORIAL = [
    r"\bexists to (?:stop|prevent|catch|protect)\b", r"\bwhich is the point\b",
    r"\bthe whole point\b", r"\bthe real (?:cost|problem|question|answer)\b",
    r"\bworth (?:noting|knowing|saying)\b", r"\bcorrectly\b", r"\brightly\b",
    r"\btellingly\b", r"\bremarkably\b", r"\bunsurprisingly\b",
    r"\bthe right call\b", r"\bis what matters\b", r"\bneedless to say\b",
    r"\bto be fair\b", r"\bin fairness\b",
]
# ORNAMENT: writerly vocabulary that carries no information. A third habit,
# next to the two above and doing a different job. EMPHASIS turns up the volume
# on a fact; EDITORIAL scores it; an ornament REPLACES it. "The follow-up is
# load-bearing" says the follow-up matters without saying what breaks without
# it, and the reader cannot act on the word. Fernando, 2026-08-24, on
# "load-bearing" - it reached the reader twice in one day.
#
# A term this fleet uses with a technical meaning stays out of this list however
# writerly it looks elsewhere, because denying it would deny the only word for
# the thing: `lens` is a reviewer in the battery, `cadence` is the release
# cadence a shelf survey has to report for a layer-4 candidate, and `blast
# radius`, `single source of truth`, `mutation`, `acceptance ledger`, `fix
# ladder`, `cast` and `retreat map` are all named parts of the process.
# "at the end of the day" is not here either - BANNED already carries it, and
# one fault should be reported once.
ORNAMENT = [
    r"\bload-bearing\b", r"\bnon-trivial\b", r"\borthogonal\b",
    r"\bfirst-class\b", r"\bnorth star\b", r"\bsurface area\b",
    r"\bin anger\b", r"\bmoves? the needle\b", r"\btable stakes\b",
    r"\btexture\b", r"\belegant(?:ly)?\b", r"\bseamless(?:ly)?\b",
    r"\bmeaningfully\b", r"\bmaterially\b", r"\bfundamentally\b",
    r"\bessentially\b", r"\bcrucially\b", r"\bnotably\b",
    r"\bimportantly\b", r"\binterestingly\b", r"\barguably\b",
    # Verb forms only. The noun ("the leverage it gives us") is a different word
    # and is not what leaks.
    r"\b(?:leverages|leveraged|leveraging)\b",
    r"\b(?:can|could|will|would|should|to|we|it|they)\s+leverage\b",
    # Sentence-opening discourse marker only: "the ruling that said nothing
    # would be built" is a relative clause and is not this fault.
    r"(?:^|(?<=[.!?]\s))\s*That said\b",
    # The reading sense only. "unpack the archive" is what the word is for.
    r"\bunpack(?:s|ed|ing)?\s+(?:the\s+|that\s+|this\s+|its\s+|their\s+)?"
    r"(?:argument|reasoning|claim|idea|question|thinking|point|logic|history)\b",
]
# Restatement. A paragraph that says one thing four ways spends the sentence
# budget on paraphrase instead of facts. Whether two sentences carry the same
# fact is semantic and a regex cannot see it - but restating an ABSENCE has a
# mechanical signature, because each paraphrase needs its own negation. Three
# negations in one paragraph is the reliable tell: "does not report", "posts no
# message", "nobody learns" are one finding wearing three coats.
NEGATION = re.compile(
    r"\b(?:not|n't|no|none|nobody|nothing|never|neither|nor|without|fails? to)\b", re.I)
MAX_PARA_NEGATIONS = 2

# Metaphor. Software does not hear, stay quiet, wake up or go blind. A figure of
# speech makes the reader translate before they can act, and the translation is
# where the meaning slips. "The room stays quiet" is one word longer than "it
# posts no message" and less exact.
METAPHOR = [
    r"\b(?:stays?|went|goes|going|fell|falls?) (?:quiet|silent|dark)\b",
    r"\b(?:hears?|heard|listens?|listening) (?:about|from|to)?\b",
    r"\b(?:speaks? up|shouts?|whispers?|screams?)\b",
    r"\b(?:wakes? up|woke up|goes to sleep|asleep at)\b",
    r"\b(?:blind to|in the dark|turns? a blind eye|flies? under)\b",
    r"\b(?:under the hood|out of the box|moving parts|low-hanging)\b",
    r"\b(?:bites?|bit) (?:us|you|back)\b",
    r"\bwearing \w+ (?:coats?|hats?)\b",
]

# Humanizer. The mechanically checkable subset of blader/humanizer's 35 AI
# writing patterns, added 2026-08-25 on Fernando's instruction so the three
# paragraphs read like a person wrote them. That skill is prose guidance and
# ships no linter, so what is enforceable had to be extracted here. A hook
# denies and cannot rewrite, so enforcement is a rejection naming the pattern,
# exactly like every other rule in this file.
#
# Three of the skill's patterns are deliberately absent because they collide
# with rules already above. Forced groups of three would deny the enumeration
# COUNTED demands, since "name the things you are counting" produces triples on
# purpose. A false "from X to Y" range cannot be told by regex from a literal
# column move, "from Review to Test". Curly quotes are left alone because the
# copy guide requires them; that conflict is Fernando's to settle, not this
# file's.
DASH = re.compile(r"[\u2013\u2014]")
NOT_BUT = re.compile(
    r"\bnot\s+(?:just\s+|only\s+|merely\s+|simply\s+)?[\w'`-]+"
    r"(?:\s+[\w'`-]+){0,4},?\s+but\b", re.I)
AI_WORDS = [
    r"\bactually\b", r"\badditionally\b", r"\bmoreover\b", r"\bfurthermore\b",
    r"\bin addition\b", r"\ba testament to\b", r"\blandscape\b", r"\bquietly\b",
    r"\bdelve\b", r"\bin the realm of\b", r"\bnavigat(?:e|es|ing) the\b",
    r"\bit'?s (?:important|crucial|essential) to (?:note|remember|understand)\b",
]
# The skill's "avoid simple is/are" rule. A dodge costs a word and loses the
# claim: "serves as the gate" leaves open whether it IS the gate.
COPULA_DODGE = [
    r"\bserves? as\b", r"\bboasts?\b", r"\bstands? as\b", r"\bacts? as a\b",
    r"\bfeatures? a\b", r"\brepresents? a\b",
]
SHALLOW_ING = re.compile(
    r"\b(?:symboliz|reflect|showcas|highlight|underscor|emphasiz|illustrat)ing\b",
    re.I)
SALES = [
    r"\bnestled\b", r"\bbreathtaking\b", r"\brobust\b", r"\bcutting-edge\b",
    r"\bgame-?chang(?:er|ing)\b", r"\bpowerful(?:ly)?\b", r"\bstunning\b",
]
VAGUE_SOURCE = re.compile(
    r"\b(?:experts?|studies|research|many|some|people)\s+"
    r"(?:believe|say|show|suggest|agree|argue|think)\b", re.I)
# "rather than" is the comparison this contract keeps asking for and is not a
# qualifier. Only the bare hedge is.

# The six rules below came out of one comment Fernando rewrote by hand on
# 2026-08-25. Each is the machine-checkable half of a rule; the half that needs
# to know what a sentence MEANS lives in the worked examples in SKILL.md
# and is not attempted here.

# SELF_HISTORY: a correction staged as a correction. The number is allowed to
# move -- "32 lines instead of the original 6" is the fact and it stays. What
# cannot appear is the apology around it, which reports our process and asks him
# to hold two numbers instead of one.
SELF_HISTORY = [
    (r"\bcorrections? to (?:what|the|my)\b", "stages the correction instead of stating the fact"),
    (r"\bas I (?:said|posted|noted|gave|wrote|mentioned|reported)\b", "cites our own earlier comment"),
    (r"\b(?:rather than|instead of) the [\d,]+ I (?:gave|posted|said|quoted|reported)\b",
     "attributes the old number to us rather than just replacing it"),
    (r"\bI (?:was|got it) wrong\b", "narrates our error"),
    (r"\b(?:earlier|previously|before) I (?:said|posted|gave|wrote|reported)\b", "cites our own earlier comment"),
    (r"\bmy (?:earlier|previous|last|original) (?:comment|estimate|note|message|figure)\b",
     "makes our earlier comment the subject"),
]

# HIS_WORDS: telling Fernando what he said. The Issues board is shared with the
# mobile team, so a correction of him lands in front of them. State the fact --
# "this lands in 5.2.0, not 5.1.6" -- and let it replace the belief silently.
# Narrow to speech nouns on purpose: "your ruling on whether the residual gets
# its own card" is a next step, not a correction, and it has to keep passing.
HIS_WORDS = [
    (r"\byour (?:\w+ )?(?:comment|message|note|estimate|guess|reply|answer|figure)\b",
     "makes the reader's own words the subject"),
    (r"\byou (?:said|wrote|asked|mentioned|noted|told me|estimated|thought)\b",
     "quotes the reader back to themselves"),
]

# OTHER_SURFACE: narrating the state of something that is not this card. A stale
# card description is real and worth acting on -- by editing it, or by reporting
# it in the return so somebody does. Spending a card sentence on it neither fixes
# it nor changes what he does next.
OTHER_SURFACE = [
    (r"\bthis card'?s description\b", "narrates this card's own description"),
    (r"\bthe card (?:still )?(?:says|describes|prescribes|reads|claims)\b", "narrates the card's contents"),
    (r"\bthe description (?:still )?(?:says|prescribes|describes|reads|claims)\b",
     "narrates a description rather than fixing it"),
]

# PROOF_OF_WORK: how we know, rather than what we found. Deliberately keyed to
# the proving verbs and nothing else -- "the probe ruled out preload" is a
# finding and must pass, while "a probe on the Electron we ship proves it" is us
# showing our work. The gap allows one clause between subject and verb; more
# than that and the sentence is about something else. A proving verb behind
# "that" or "which" is a relative clause naming a check, not an assertion about
# one: "the check that proves the shell renders runs on macOS only" tells him
# which platform is covered and changes what he watches, which is the whole test
# for whether a caveat earns its place.
PROOF_OF_WORK = [
    (r"\b(?:tests?|probes?|suite|checks?|assertions?)\b[^.]{0,50}?\b"
     r"(?<!that )(?<!which )(?:proves?|proved|confirms?|confirmed|verif\w+)\b",
     "reports how we know instead of what we found"),
    (r"\bwe (?:ran|checked|verified|measured|confirmed|tested|reproduced)\b",
     "narrates our own verification"),
    (r"\b(?:ran|re-ran|executed|installed|invoked)\s+(?:the\s+|its\s+|[\w-]+'?s?\s+)*"
     r"(?:suite|tests?|CI|package|build|checks?)\b",
     "narrates the activity rather than its result"),
    (r"\b(?:proven|verified|confirmed) (?:locally|by|in test)\b",
     "reports how we know instead of what we found"),
]

# OPEN_CHOICE: an option named and left flat is work handed back. Every choice
# carries a lean and the one thing that would flip it, so he can override in a
# word instead of reconstructing the decision. Checked per paragraph, because a
# lean three paragraphs from its option is not attached to it.
OPTION = re.compile(
    r"\b(?:we (?:could|can|might)|one option|the alternative|either\b[^.]{0,60}\bor\b)\b", re.I)
LEAN = re.compile(
    r"\b(?:I would|I'd|I recommend|my read|worth [^.]{0,30}only if|only fold|"
    r"the cheaper (?:direction|one)|leaning)\b", re.I)

# BARE_IDENTIFIER: a name he could grep for reads as prose until it is marked.
# Files, and symbols too -- `notificationNavigationDispatched` is the most useful
# thing in the sentence it appears in, and bare it looks like a long word.
#
# camelCase requires a lowercase start, which keeps product names out: TestFlight
# and App Store are PascalCase or two words and neither is greppable. The dotted
# form catches `SessionPersistenceStore.save` and `valet.setObject`.
#
# Marking them also makes the jargon scan below safe. A term of art inside
# backticks is a symbol, and stripping code spans first is what tells them apart.
FILE_EXT = (r"js|jsx|ts|tsx|rb|py|swift|json|yml|yaml|toml|erb|css|scss|"
            r"kt|java|go|rs|sh|plist|xcconfig")
# Platform and product names look like camelCase and are not symbols. There is
# nothing behind `macOS` to find, which is the test rule 15 turns on.
NOT_AN_IDENTIFIER = {
    "macos", "ios", "ipados", "watchos", "tvos", "visionos", "iphone", "ipad",
    "ipod", "imac", "macbook", "appstore", "testflight", "javascript",
    "typescript", "github", "gitlab", "npm", "jquery", "nodejs",
}
BARE_IDENTIFIER = re.compile(
    rf"\b[\w-]+\.(?:{FILE_EXT})\b"
    r"|\b[a-z][a-z0-9]*(?:[A-Z][A-Za-z0-9]*){1,}\b"
    r"|\b[A-Za-z][A-Za-z0-9]*\.[a-z][A-Za-z0-9]*(?:\(\))?")

# JARGON_OF_ART: an industry term where an ordinary word says the same thing.
# "The foreground fan-out never started" became "the foreground tracking never
# started" -- and the symbol beside it survived untouched, because a real name is
# not jargon. The test is whether he could search for it: a symbol he can, a term
# of art he cannot.
#
# Seeded from one observation and meant to grow through defect analysis rather
# than by guessing at a vocabulary nobody has used yet.
JARGON_OF_ART = [
    (r"\bfan[- ]?outs?\b", "the work it starts"),
    (r"\bback[- ]?pressure\b", "what is being slowed down and why"),
    (r"\bre-?hydrat(?:e|es|ed|ing|ion)\b", "what gets loaded back"),
    (r"\b(?:un)?marshall?(?:s|ed|ing)?\b", "what is being converted"),
    (r"\bidempotent\b", "safe to run twice"),
    (r"\bcoalesc(?:e|es|ed|ing)\b", "merged, or waited out"),
]




def unmarked_prose(text):
    """The text with code spans, anchors and URLs removed.

    A name already inside backticks or a `<code>` span is marked, and a path
    inside a link is part of the proof rather than the sentence.
    """
    out = re.sub(r"<code\b[^>]*>.*?</code>", " ", text, flags=re.I | re.S)
    out = re.sub(r"`[^`]*`", " ", out)
    out = re.sub(r"<a\b[^>]*>.*?</a>", " ", out, flags=re.I | re.S)
    out = re.sub(r"\]\([^)]*\)", " ", out)
    return URL.sub(" ", out)



# PROCESS_STATUS: sentences whose subject is the state of our own work rather
# than the state of the code. "369 is green", "the macOS build settled the risk",
# "merging is yours" -- each is true, none of them is a finding, and all three
# survived every other check on this file until Fernando cut them by hand.

# CREDIT: who supplied what. "Your token was half of it", "the other half is
# persist-credentials" -- the sentence is about the collaboration rather than
# about the code, and by the time he reads it the halves have already met.
CREDIT = [
    (r"\byour \w+ was (?:half|part|most|all) of\b", "credits the reader for a part of the work"),
    (r"\bthe other half (?:is|was)\b", "splits the work into contributions"),
    (r"\b(?:thanks to|down to) your\b", "thanks the reader inside a finding"),
    (r"\byou (?:supplied|provided|added) the\b", "narrates who did which piece"),
]

PROCESS_STATUS = [
    (r"\b(?:is|are|came back|came in|went)\s+green\b", "reports a build status as the finding"),
    (r"\b(?:settled|cleared|closed out|put to bed) the risk\b", "reports that a phase finished"),
    (r"\b(?:merging|the merges?|shipping|releasing|the calls?|the decisions?)\s+"
     r"(?:is|are) (?:yours|his|mine|ours)\b",
     "announces whose turn it is instead of what to do"),
    (r"\bpart \d+ (?:rests on|depends on|is what|hangs on)\b",
     "frames the finding as an internal dependency between our own parts"),
    (r"\bis the same [\w.]+ it was\b", "reports that something did not change"),
]

# The LOC inventory. One figure sizes a change; three are the arithmetic under
# it, and at merge time even the one is usually noise -- the code already exists
# and its size changes nothing the reader does. Your own measurements hold the real number
# from git either way, so nothing is lost by leaving it off the card.
LOC_FIGURE = re.compile(
    r"\b[\d,]+\s+(?:lines?|insertions?|deletions?|(?:lines? )?"
    r"(?:added|deleted|removed|inserted|changed))\b", re.I)

QUALIFIER = [
    r"\bsomewhat\b", r"\bfairly\b", r"\brather\b(?!\s+than)", r"\bquite\b",
    r"\bgenerally speaking\b", r"\bin many ways\b", r"\bto some extent\b",
    r"\bmore or less\b",
]

# Named referents. "both checks" makes him ask which two. A quantified plural
# stands only when the sentence also names the things it counts.
# The counter has to be followed by a NOUN. "either goes unrecorded" and "both
# runs green" are not counted-but-unnamed, and denying them costs a round each.
# Nothing here does part-of-speech tagging, so the verbs that actually turn up
# after these counters are listed and skipped.
VERBS = (r"goes|does|is|was|has|gets|takes|makes|needs|runs|says|means|comes|"
         r"stays|keeps|lands|reads|writes|holds|sits|ships|fires|costs|counts|"
         r"matches|carries|leaves|ends|starts|stops|fails|passes|wins|looks|"
         r"points|names|calls|shows|tells|asks|wants|works|helps|adds|drops")
COUNTED = re.compile(
    r"\b(?:both|either|the two|all three|all four|the three)\s+"
    r"(?:the\s+)?(?!(?:" + VERBS + r")\b)[a-z][a-z-]*s\b", re.I)

# The same failure with the noun supplied and the items still missing:
# "those five instrumentation parts", "the four events". He asked "what
# instrumentation parts?" within a minute of reading one. A count only
# earns its place when the comment itself enumerates what it counts, so
# this fires unless the body carries a list or the sentence introduces one.
POINTED = re.compile(
    r"\b(?:those|these|the|all)\s+"
    r"(?:two|three|four|five|six|seven|eight|nine|ten|\d{1,3})\s+"
    r"(?:[a-z][a-z-]*\s+){0,2}[a-z][a-z-]*s\b", re.I)

URL = re.compile(r"https?://\S+")
SENT = re.compile(r"[.!?]+(?:\s|$)")
TAG = re.compile(r"<[^>]+>")
CODE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.I | re.S)


# The empty sentence. Fernando, 2026-08-24, quoting one of mine: "One gap this
# makes reachable, which I left alone rather than widen a reviewed branch." He
# said he would not know how to catalogue it. The sentence asserts nothing; its
# whole job is to announce that a sentence is coming, and the fact it stood in
# for (the picker never re-derives authorization on return) arrived in the NEXT
# sentence. Two faults were separable in it:
#
# (a) No finite main verb. Every verb - makes, left, widen - sits inside a
#     subordinate clause, so nothing predicates anything.
# (b) A placeholder subject. "gap" stands in for the thing instead of naming it,
#     and the sentence carries no number, no path, no identifier.
#
# ONLY (b) IS IMPLEMENTED, and (a) was built, measured and thrown away. Over the
# 53 comments this bot posted to the on-call cards on 2026-08-22 through 08-24, a
# verbless test fired 13 times and was right ONCE - on the sentence above. The
# twelve others were ordinary sentences with ordinary main verbs, and the two
# causes are not tunable:
#
#   "That page event only drives the adapter" - `that` here is a determiner, and
#   "Per hour that family went up" and "That was my error" are the same word
#   again as determiner and pronoun. Telling those from the relativizer in "the
#   check that runs" is a tagging problem.
#
#   "They name which mechanism it is", "Keep 262 anyway", "Then decide the
#   remaining fix" - bare-form main verbs. A plural present, an imperative and a
#   noun are the same string, so no word list can find the predicate; adding one
#   invents a false verb somewhere else. VERBS below works because it is asked
#   one narrow question in one narrow slot (what follows a counter), not to parse
#   a sentence.
#
# A guard nobody can satisfy gets worked around - that is how the bullet-list
# exemption happened - and 12 false denials in 53 comments is that guard. Left
# out deliberately; do not re-add it without measuring it again.
# No possessives. A possessive names an owner, which makes the noun referential
# rather than a stand-in: "My note that night predicted a different trigger" is
# a fact about a specific note and the only false denial this rule produced over
# the 53 posted comments it was measured against.
# UNINTRODUCED: a generic system noun whose FIRST appearance is definite. "The
# reopen", "the job", "the tool", "that map" -- each points back at something the
# reader was never given, and the writer only knows what they are because it
# spent an hour inside them.
#
# This is a different failure from the placeholder subjects `empty_sentences`
# catches, which is why that check passed the comment that produced this one:
# those sentences named plenty -- versions, shas, counts -- and simply never said
# what they were about.
#
# Scoped to generic system nouns rather than every noun, and satisfied by any
# indefinite or marked first mention: "a stronger test ... the old test" is how
# it is done, and passes. Words that carry their own meaning in a product
# sentence -- fix, path, build, release, card, change -- are deliberately absent.
SYSTEM_NOUN = (r"job|tool|map|set|feed|spread|pipeline|hook|script|harness|"
               r"sweep|scan|gate|queue|worker|watcher|collector")
# Adjectives sit between the article and the noun on both sides -- "a nightly
# job" introduces the thing that "the job" later points at, and requiring the
# article to be adjacent made every real introduction invisible.
ADJECTIVES = r"(?:[\w-]+\s+){0,2}"
DEFINITE_FIRST = re.compile(
    r"\b(?:the|that|this)\s+" + ADJECTIVES + r"(" + SYSTEM_NOUN + r")\b", re.I)
ANY_FIRST = re.compile(
    r"\b(?:a|an|one|its|his|our|their|[\w-]+'s)\s+" + ADJECTIVES +
    r"(" + SYSTEM_NOUN + r")\b", re.I)


def unintroduced(text):
    """System nouns the comment points at before it ever names them."""
    plain = unmarked_prose(text)
    found = []
    for m in DEFINITE_FIRST.finditer(plain):
        noun = m.group(1).lower()
        if any(noun == a.group(1).lower() and a.start() < m.start()
               for a in ANY_FIRST.finditer(plain)):
            continue
        if noun not in [f.lower() for f in found]:
            found.append(m.group(0))
    return found


DETERMINER = (r"a|an|the|this|that|these|those|one|two|three|four|five|six|"
              r"seven|eight|nine|ten|another|each|every|some|any|no|both|"
              r"several|few|many|most|\d+")
# The nouns that stand in for the thing instead of naming it.
PLACEHOLDER = (r"gaps?|things?|points?|parts?|half|halves|pieces?|items?|"
               r"notes?|corrections?|follow-?ups?|questions?|issues?|"
               r"wrinkles?|catch(?:es)?|upshots?|takeaways?")
ANNOUNCEMENT = re.compile(
    r"^(?:" + DETERMINER + r")\s+(?:" + PLACEHOLDER + r")\b", re.I)

# What a reader can act on: a number, a quoted span, a path, an identifier, a
# proper noun. A sentence carrying none of them has named nothing, and a
# placeholder subject on top of that leaves it with nothing to say. The evidence
# half is what keeps the rule off the sentences that merely START this way -
# "Two things turned up in HEY-DESKTOP-64Q" names its subject and stands.
EVIDENCE = re.compile(
    r"\d|`[^`]+`|/[\w.-]+/|\b\w+[._]\w+\b|\b[a-z]+[A-Z]\w*\b")
PROPER = re.compile(r"\b[A-Z][A-Za-z0-9-]+")


def sentences(para):
    """The paragraph's sentences, with the markup that is not prose removed.

    Mentions go first: a mention expands to an attachment carrying a person's
    name, and reading that name as a proper noun would let every sentence
    sharing a paragraph with one claim it had named something.
    """
    text = CODE.sub(r"`\1`", MENTION.sub(" ", para))
    text = TAG.sub(" ", URL.sub(" ", text))
    return [" ".join(s.split()) for s in SENT.split(text) if s.strip()]


def names_something(sent, blind=0):
    """Whether the sentence points at anything the reader can act on.

    `blind` masks the opening determiner and placeholder without moving the rest
    of the sentence, because the count in "2 corrections to what I put here
    earlier" is part of the stand-in and not evidence of anything. Masking with
    spaces rather than slicing keeps the offsets, so a proper noun that opens the
    remainder still reads as non-initial.
    """
    sent = " " * blind + sent[blind:]
    if EVIDENCE.search(sent):
        return True
    return any(m.start() > 0 for m in PROPER.finditer(sent))


def empty_sentences(prose):
    """Sentences whose subject is a placeholder and which name nothing."""
    found = []
    for para in prose:
        for sent in sentences(para):
            if len(sent.split()) < 3:
                continue
            opener = ANNOUNCEMENT.match(sent)
            if opener and not names_something(sent, opener.end()):
                found.append(sent)
    return found


# The method preamble. Fernando, 2026-08-24, on a sentence of mine from that
# night: "I don't need meta-statements like 'Measuring the file instead of the
# culprit:'. The rest of the sentence is useful enough." A sentence-initial
# clause narrating the act of measuring or looking, handed to the finding by a
# colon or a comma. The finding stands on its own; the preamble says how we got
# it, which is your process and belongs in your notes with the rest of it.
#
# Only the forms he listed, spelled out rather than stemmed. Bare "read" and
# "check" are deliberately absent: "Read the thread, then rule" is an imperative
# in a next-step paragraph, not a preamble, and stemming would swallow it.
INSPECTION = (r"measuring|reading|re-reading|checking|querying|queried|measured|"
              r"counting|counted|running|re-running|ran|looking|grepping|"
              r"diffing|inspecting|comparing|testing|verifying|scanning|"
              r"sampling|pulling|fetching")

# Two handoffs, held to different lengths, because they carry different risk.
#
# A colon after a sentence-initial participle is unambiguous - a gerund SUBJECT
# is never handed to a colon before its own verb - so the clause may run long.
# "Reading every event in the window instead of a sample:" is ten words and is
# the fault exactly.
#
# A comma is where the ambiguity lives, and it is the one Fernando drew a line
# around: "Running the suite serially avoids the port collision" is a claim about
# the system whose gerund is the SUBJECT, and a trailing "..., which is why we
# serialize" would otherwise pull it in. Two things keep it out - the clause must
# be short (his real one, "Queried directly,", is two words) and it must carry no
# finite verb of its own. `[^\s:,]+` stops either pattern at the first
# punctuation, so what matches is the opening clause and not some later one.
METHOD_PREAMBLE = [
    (re.compile(r"^(?:" + INSPECTION + r")\b((?:\s+[^\s:,]+){0,12})\s*:\s+(.+)$", re.I), False),
    (re.compile(r"^(?:" + INSPECTION + r")\b((?:\s+[^\s:,]+){0,4})\s*,\s+(.+)$", re.I), True),
]
# The generous -s/-ed shape again, and generous is again the safe direction: a
# plural noun misread as a verb costs a preamble we do not catch, while a real
# verb missed would deny a sentence about the system. Never applied to the
# opening word itself, which is a participle and ends that way by definition.
PREAMBLE_VERB = re.compile(
    r"\b(?:" + VERBS + r"|is|are|was|were|has|have|had|does|did|do|will|would|"
    r"can|could|should|must|may|might|\w{3,}(?:ed|s))\b", re.I)


def method_preambles(prose):
    """Sentence openings that narrate the looking instead of stating the finding."""
    found = []
    for para in prose:
        for sent in sentences(para):
            for pattern, veto in METHOD_PREAMBLE:
                m = pattern.match(sent)
                if not m:
                    continue
                if veto and PREAMBLE_VERB.search(m.group(1)):
                    continue
                # The finding after the handoff has to be able to stand alone; a
                # participial clause that IS the whole sentence is not this fault.
                if len(m.group(2).split()) < 3:
                    continue
                found.append((sent[:m.start(2)].rstrip(), m.group(2)))
                break
    return found


# A Basecamp record link whose visible text is the URL itself. Basecamp resolves a
# pasted link to the record's name in its own composer, but posting through the API
# does not: the server only autolinks it (class="autolinked" data-behavior="truncate",
# verified by probe 2026-08-21). So the name has to be fetched and used as the anchor
# text, or the reader gets a bare id to decode.
BARE_LINK = re.compile(
    r'<a[^>]+href="(https://(?:app\.basecamp\.com|3\.basecampapi\.com)/[^"]+)"[^>]*>\s*'
    r'(?:https?://|#?\d{6,})', re.I)


# A pull request or a Sentry issue named by its id carries a link. Fernando,
# 2026-08-24: "why are we not catching that all PRs should have links?", and on
# scope: "Both PRs and Sentry issues should be linked." Four PR references went
# out that evening as bare text - "Pull request 1667", "Pull request 1648",
# "PR 133", "PR 1666" - and six Sentry ids alongside them. Each one sends him to
# go and find it. The discipline had been holding only when he asked for links by
# name in the message that started the work, which is not a discipline.
#
# This is the inverse of BARE_LINK above, not a duplicate of it: that one refuses
# a link whose visible text is its own URL and demands the record's name instead,
# and this one refuses the absence of a link. They compose, and the corpus already
# carries the shape both of them want - `hey-ios#1665` as the anchor text over a
# github pull URL.
#
# Basecamp cards and comments are NOT here. He ruled on pull requests and Sentry
# issues and said nothing about ids, and the corpus says there is nothing to fix:
# all thirteen ten-digit record ids in it are already inside a link, none bare.
#
# The bare number leaning on a repo established earlier ("Your 1666 shipped a
# second one doing the same job") is deliberately NOT matched. Telling that from
# a version, an event count or a build number needs a number detector, and a
# wrong one denies arithmetic he asked for. It under-reaches by one shape, and
# that costs nothing here: the comment carrying it also says "Pull request 1667".
ANCHOR_TEXT = re.compile(r"<a\b[^>]*>.*?</a>", re.I | re.S)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
PR_REFERENCE = re.compile(
    r"\bpull\s+requests?\s+#?(\d+)"      # pull request 1667
    r"|\bPRs?\s+#?(\d+)"                 # PR 133
    r"|\b[\w.-]+/[\w.-]+#(\d+)"          # basecamp/bc3#12863
    r"|\b[a-z][\w.-]*#(\d+)",            # hey-ios#1665
    re.I)
# Case-sensitive, and that is load-bearing rather than tidy: the internal slugs in this
# fleet are named `bc3-ios-web-view-bridge-diagnostic-fires-on-live-bridges`, and
# a case-blind version of this reads `bc3-ios-web` as a Sentry short id and denies
# every comment that names its own internal record.
SENTRY_ID = re.compile(
    r"\b(?:BC3|BC4|HEY)-(?:IOS|DESKTOP|ANDROID|WEB|ELECTRON)-[A-Z0-9]{2,6}\b")
REFERENCES = [("pull request", PR_REFERENCE), ("Sentry issue", SENTRY_ID)]


def unlinked_references(text):
    """PR and Sentry references outside a link, with nothing else linking that id.

    Deliberately forgiving about WHERE the link is. A comment that links the issue
    once and then talks about it in prose has already saved him the lookup, so
    only an id nothing points at is refused. Bare URLs count as links because
    Basecamp autolinks them, and they are stripped before the scan for the same
    reason.
    """
    body = MENTION.sub(" ", text)
    outside = URL.sub(" ", TAG.sub(" ", ANCHOR_TEXT.sub(" ", body)))
    targets = " ".join(HREF.findall(body) + URL.findall(body))
    found = []
    for kind, pattern in REFERENCES:
        for m in pattern.finditer(outside):
            identifier = next((gr for gr in m.groups() if gr), m.group(0))
            if re.search(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", targets):
                continue
            found.append((kind, " ".join(m.group(0).split())))
    return found


# Tier one of the verifier question, and the only tier that belongs in a hook:
# the anchor text has to agree with the href it sits on. "Pull request 1667"
# pointing at /pull/1666 is a transposition no reading catches, and HEY-IOS-669
# against HEY-IOS-663 is one character. Both are offline, deterministic and cost
# nothing.
#
# Scoped to github pull/issue links and Sentry issue links, because those are the
# two whose id convention is known. A Basecamp link carries a record TITLE as its
# anchor text, and a title with a number in it ("... | May 29") would be read as a
# claim about the card id and denied.
#
# The claim is only read off the END of a github label - every one in the corpus
# is "<repo words> <number>": "Hotwire Native 262", "bc3 12863", "core-ios 133",
# "PR 1648", "hey-ios#1665". A number anywhere else in the label is describing
# something, not naming the target.
LINK_TARGETS = [
    ("pull request",
     re.compile(r"github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/(\d+)", re.I),
     lambda label: re.findall(r"#?(\d{2,})\s*$", label)),
    ("Sentry issue",
     re.compile(r"sentry\.io/issues/([A-Za-z0-9-]+)", re.I),
     lambda label: SENTRY_ID.findall(label)),
]


def mismatched_links(text):
    """Links whose visible id is not the id the href points at."""
    found = []
    for m in ANCHOR_TEXT.finditer(MENTION.sub(" ", text)):
        href = HREF.search(m.group(0))
        if not href:
            continue
        label = " ".join(TAG.sub(" ", m.group(0)).split())
        for kind, target, shown_ids in LINK_TARGETS:
            hit = target.search(href.group(1))
            if not hit:
                continue
            shown = shown_ids(label)
            if not shown:
                continue
            if any(one.upper() == hit.group(1).upper() for one in shown):
                continue
            found.append((kind, label, hit.group(1)))
            break
    return found


# Tier two: does the link's target exist. Fernando, 2026-08-24, on the offline
# check and this one together: "Ok let's try both of these solutions."
#
# It reads the HREFS ONLY, never the prose, and that is what makes it possible at
# all. "Pull request 1667" does not name a repository and could be any of ours,
# so there is nothing to ask about; the presence rule above already refuses a
# reference carrying no link, so by the time this runs every reference the reader
# can follow is a URL naming its owner, its repo and its number.
#
# GITHUB ONLY, AND NO SENTRY BRANCH THAT ALWAYS PASSES. Probed 2026-08-24: this
# environment has `gh` authenticated and answering in under a second, and for
# Sentry it has no SENTRY_AUTH_TOKEN, no sentry-cli and no ~/.sentryclirc, while
# the Sentry MCP server a session can call is not reachable from a PreToolUse
# hook at all. A check that cannot fail reads as coverage, which is worse than no
# check, so Sentry ids get the two offline tiers and nothing more.
#
# FAILS OPEN, AND LEAVES A TRACE. A 404 is an answer, and it denies. A timeout, a
# missing `gh`, a rate limit or an expired token is NOT an answer: the post goes
# through, because a hook that blocks a legitimate comment on a network blip is a
# hook that gets worked around, and every other network path in this file already
# fails open. What makes that defensible rather than theatre is the trace - the
# miss is reported to the caller rather than silently dropped.
# cite-check.py reserves a third verdict for exactly this (UNRESOLVED, "silence
# would read as coverage"); a PreToolUse hook has only allow and deny, so the
# third verdict has to be written down instead of returned.
GH = os.environ.get("THREE_PARAGRAPHS_GH", "gh")
VERIFIED = os.path.expanduser(os.environ.get(
    "THREE_PARAGRAPHS_LINK_CACHE", "~/.cache/three-paragraphs/verified-refs.json"))
VERIFY_TIMEOUT = 5
VERIFY_BUDGET = 12
# `issues/N` rather than `pulls/N`: every pull request is an issue on this
# endpoint, so one call answers for both link shapes.
GH_TARGET = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)", re.I)


def github_has(owner, repo, number, deadline):
    """True, False, or None when nothing definite came back."""
    left = deadline - time.time()
    if left <= 0:
        return None
    try:
        out = subprocess.run(
            [GH, "api", f"repos/{owner}/{repo}/issues/{number}"],
            capture_output=True, text=True, timeout=min(VERIFY_TIMEOUT, left))
    except Exception:
        return None
    if out.returncode == 0:
        return True
    if '"status":"404"' in out.stdout or "HTTP 404" in out.stderr:
        return False
    return None


# Off unless asked for. Every other rule reads the text in front of it; this one
# shells out to `gh` and waits on GitHub, which makes a gate slow, dependent on a
# network and on someone being logged in, and prone to reporting a private repo as
# a missing one. It is worth having where a wrong PR number costs a reader a click
# into nothing -- so it stays, behind a switch, rather than being the reason
# somebody turns the whole contract off.
#
#   THREE_PARAGRAPHS_VERIFY_LINKS=1
VERIFY_LINKS = os.environ.get("THREE_PARAGRAPHS_VERIFY_LINKS") == "1"


def unresolvable_links(text, card=None):
    """Linked pull requests GitHub does not have. Asks the network; fails open."""
    if not VERIFY_LINKS:
        return []

    body = MENTION.sub(" ", text)
    targets, seen = [], set()
    for href in HREF.findall(body) + URL.findall(body):
        m = GH_TARGET.search(href)
        if not m:
            continue
        key = f"{m.group(1)}/{m.group(2)}#{m.group(3)}"
        if key not in seen:
            seen.add(key)
            targets.append((key, m.group(1), m.group(2), m.group(3)))
    if not targets:
        return []
    try:
        cache = json.load(open(VERIFIED))
    except Exception:
        cache = {}
    deadline = time.time() + VERIFY_BUDGET
    missing, fresh = [], False
    for key, owner, repo, number in targets:
        if cache.get(key):
            continue
        answer = github_has(owner, repo, number, deadline)
        if answer is True:
            cache[key] = True
            fresh = True
        elif answer is False:
            missing.append(key)
        else:
            record_debt(card, "unverified-link",
                        f"could not confirm {key} exists; the comment posted "
                        "without that check")
    if fresh:
        try:
            json.dump(cache, open(VERIFIED, "w"))
        except Exception:
            pass
    return missing


MAX_TOTAL_WORDS = 180
MAX_PARA_WORDS = 90
MAX_PARA_SENTENCES = 5
MAX_SENTENCE_WORDS = 25


# Self-owned next steps. A comment that ends "none from you" or "mine, not yours"
# declares work the session still owes. The guard runs before the comment posts
# and cannot watch what happens after it, so it records the debt instead: every



# The body contract itself, lifted out of main so a second surface can be held
# to it without a second copy of it. A second surface calls this: when the
# prose moved from the card to a ping, the contract had to move with it, and a
# forked copy would have drifted the first time either was amended.
#
# A pure move. Every check below ran in main in this order before 2026-08-26.
# The length caps are arguments because the ping is a tighter surface than the
# card: same three paragraphs, fewer words in each. Everything else in the
# contract is identical on both, which is why there is one function rather than
# two files that drift.
def contract_problems(paras, card=None,
                      max_para_words=MAX_PARA_WORDS,
                      max_total_words=MAX_TOTAL_WORDS):
    text = "\n".join(paras)
    problems = []
    prose = [p for p in paras if not NONPROSE.search(MENTION.sub(" ", p))]
    extras = len(paras) - len(prose)
    if not prose:
        problems.append("no prose paragraphs. A table or an image is additional to the "
                        "explanation, never a replacement for it.")
    elif len(prose) > 3:
        problems.append(f"{len(prose)} prose paragraphs; three is the ceiling "
                        "(explanation, then a paragraph naming the next step). Fewer is "
                        "fine and often right — a comment with two things in it gets two "
                        "paragraphs, and padding to three is how a short answer becomes a "
                        f"long one. Tables and images are additional — {extras} found. A "
                        "bullet list is not additional; it is prose that skipped "
                        "the caps, and it counts.")
    if LISTS.search(text):
        problems.append(
            "bullet list. Lists are exempt from the word and sentence caps, so a "
            "list is where a comment goes to say more than three paragraphs allow. "
            "Say it in the prose or leave it in your notes. Tables and images "
            "still stand.")

    low = text.lower()
    hits = [b for b in BANNED if b in low]
    if hits:
        problems.append("soft-ask/CTA/filler phrase(s): " + ", ".join(repr(h) for h in hits))
    if EMOJI.search(text):
        problems.append("contains emoji")
    for pat, why in JARGON:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"internal vocabulary - {why} ({m_.group(0)!r}). "
                            "It only parses with your notes open. Say the thing "
                            "in the reader's own terms.")
    # No exemption. The original carried one for a comment that declared itself a
    # postmortem, because measuring the work IS what a postmortem is for. That is
    # a shape one team has; if you have it too, pass the exemption in rather than
    # letting every comment claim it by opening with the right word.
    for pat, why in ACCOUNTING:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"effort accounting - {why} ({m_.group(0)!r}). What a "
                            "the work cost you and what it touched are both material for "
                            "your own notes. If the number changes their decision, give them "
                            "the decision, not the arithmetic; the consequence in "
                            "the same paragraph is what they can act on, and it keeps "
                            "its place once the count around it goes.")

    for pat, why in LEDGER:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"bookkeeping sentence - {why} ({m_.group(0)!r}). "
                            "What the reader opens is a decision surface, not a record of "
                            "our process. State the finding, not where it was filed.")
    for m_ in BARE_LINK.finditer(text):
        problems.append(
            f"link shows its URL instead of the record's name ({m_.group(1)[:60]}...). "
            "Basecamp resolves a pasted link in its own composer; posting through the "
            "API does not, so fetch the target's title and use it as the anchor text.")

    for kind, reference in unlinked_references(text):
        problems.append(
            f"{kind} named without a link ({reference!r}). He has to go and find "
            "it. Link it, with the id as the anchor text - `repo#N` over the pull "
            "URL, the short id over the Sentry issue URL - not the bare URL.")

    for kind, label, target in mismatched_links(text):
        problems.append(
            f"link says {label!r} but points at {kind} {target!r}. One of the two "
            "is wrong and reading will not catch which. Check the id you meant "
            "and make the anchor text and the href name the same thing.")

    for key in unresolvable_links(text, card):
        problems.append(
            f"linked pull request does not exist ({key}). GitHub answered 404. "
            "Check the number before they follow it.")

    for i, para in enumerate(prose, 1):
        negs = NEGATION.findall(TAG.sub(" ", URL.sub("", para)))
        if len(negs) > MAX_PARA_NEGATIONS:
            problems.append(
                f"paragraph {i} negates {len(negs)} times ({', '.join(repr(n) for n in negs[:4])}). "
                "Restating one absence in different words spends sentences without "
                "adding facts. Say it goes missing once, then say what follows from it.")

    for pat in METAPHOR:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"metaphor ({m_.group(0)!r}). Software does not hear or "
                            "stay quiet. Say the literal thing it does or does not do.")

    enumerates = "<li" in text or "<td" in text
    for sent in re.split(r"(?<=[.;])\s+", TAG.sub(" ", text)):
        m_ = COUNTED.search(sent)
        if m_ and "`" not in sent and "<code" not in sent:
            problems.append(f"counted but unnamed ({m_.group(0)!r}). Name the things "
                            "you are counting, or they have to go and look them up.")
            break

    if not enumerates:
        for sent in re.split(r"(?<=[.;:])\s+", TAG.sub(" ", text)):
            m_ = POINTED.search(sent)
            if m_ and ":" not in sent and "`" not in sent:
                problems.append(
                    f"points at a list they cannot see ({m_.group(0)!r}). A count "
                    "with no enumeration makes them ask what they are. Name them, "
                    "list them, or say the one that matters and drop the number.")
                break

    for pat in EMPHASIS:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"emphasis written for effect ({m_.group(0)!r}). "
                            "State the fact without the intensifier; if it needs one "
                            "to land, the fact is not carrying its own weight.")
    for pat in EDITORIAL:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"editorial - judges instead of reporting ({m_.group(0)!r}). "
                            "Report the finding and let them judge it.")
    for pat in ORNAMENT:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(
                f"ornament ({m_.group(0).strip()!r}) - the word is doing the work "
                "the fact should do. It tells them the thing matters without "
                "telling them what breaks, what it costs or what it touches. "
                "Say that, and the word stops being needed.")

    if DASH.search(TAG.sub(" ", CODE.sub(" ", text))):
        problems.append(
            "em or en dash. A dash is where two sentences were glued together, "
            "and the join is the tell. Use a period, a comma or a colon, and say "
            "which relation you meant.")
    m_ = NOT_BUT.search(TAG.sub(" ", text))
    if m_:
        problems.append(
            f"not-X-but-Y construction ({m_.group(0)!r}). It spends a clause "
            "denying something nobody claimed. State Y and stop.")
    for pat in AI_WORDS:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(
                f"AI-tell word ({m_.group(0)!r}). It is a connective carrying no "
                "fact. Cut it, or replace it with the relation it stands in for.")
    for pat in COPULA_DODGE:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(
                f"dodges is/are ({m_.group(0)!r}). Say what the thing IS. The dodge "
                "costs a word and leaves the claim hedged.")
    m_ = SHALLOW_ING.search(text)
    if m_:
        problems.append(
            f"shallow -ing analysis ({m_.group(0)!r}). The participle asserts a "
            "relation without arguing it. Name what follows from the fact.")
    for pat in SALES:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(
                f"sales word ({m_.group(0)!r}). It rates the thing instead of "
                "describing it. Give the measurement they would rate it by.")
    m_ = VAGUE_SOURCE.search(TAG.sub(" ", text))
    if m_:
        problems.append(
            f"vague source ({m_.group(0)!r}). Name who, or drop the appeal and "
            "carry the claim on its own link.")
    for pat in QUALIFIER:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(
                f"hedge ({m_.group(0)!r}). It softens a fact they have to act on. "
                "State the fact, or state the uncertainty as a number.")

    # Prose only. A table cell is a noun phrase by design and a caption names
    # nothing; counting either as an empty sentence would deny the tables he
    # asked for.
    for sent in empty_sentences(prose):
        problems.append(
            f"empty sentence ({sent!r}) - its subject stands in for the thing "
            "instead of naming it, and it carries no number, identifier, path or "
            "proper noun, so its only job is to announce the sentence after it. "
            "Put the fact in this sentence, or delete it and let the next one "
            "stand on its own.")

    for preamble, finding in method_preambles(prose):
        problems.append(
            f"method preamble ({preamble!r}) - it narrates the looking, and they "
            "did not ask how the number was got. The finding carries itself: "
            f"start the sentence at {finding.split()[0]!r}. How it was measured "
            "belongs in your own notes.")

    for pat, why, fix in VOICE:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"passive or ambiguous about the action - {why} "
                            f"({m_.group(0)!r}). {fix}")

    for pat, why in SELF_HISTORY:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"staged correction - {why} ({m_.group(0)!r}). Replace the "
                            "old fact with the new one and say nothing about the swap: "
                            "'32 lines instead of the original 6' carries the change "
                            "without asking them to hold two numbers.")
    for pat, why in HIS_WORDS:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"corrects the reader in front of others - {why} ({m_.group(0)!r}). "
                            "This board is shared. State the fact and let it replace the "
                            "belief: 'this lands in 5.2.0, not 5.1.6'.")
    for pat, why in OTHER_SURFACE:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"narrates another surface - {why} ({m_.group(0)!r}). A card "
                            "or description that is wrong gets fixed, or reported in your "
                            "return so somebody fixes it. A sentence about it here does "
                            "neither.")
    for pat, why in PROOF_OF_WORK:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"proof of our own work - {why} ({m_.group(0)!r}). He is not "
                            "auditing the method. State the finding; a caveat stays only "
                            "when it changes their next move.")
    for pat, why in CREDIT:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"credits the collaboration - {why} ({m_.group(0)!r}). By the "
                            "time they read this the halves have already met. The sentence "
                            "is about you and the reader; it needs to be about the work.")
    for pat, why in PROCESS_STATUS:
        m_ = re.search(pat, text, re.I)
        if m_:
            problems.append(f"process status, not a finding - {why} ({m_.group(0)!r}). "
                            "The subject of a sentence here is the product or the person "
                            "using it, never the state of our own work.")
    figures = LOC_FIGURE.findall(text)
    if len(figures) > 1:
        problems.append(f"{len(figures)} size figures; one sizes a change and the rest are "
                        "the arithmetic under it. Keep the single number that carries the "
                        "decision, or none at all when the code already exists and its "
                        "size changes nothing they do.")

    for i, para in enumerate(prose, 1):
        m_ = OPTION.search(para)
        if m_ and not LEAN.search(para):
            problems.append(f"paragraph {i} names an option with no lean ({m_.group(0)!r}). "
                            "Every open choice carries a recommendation and the one thing "
                            "that would flip it, so they can override in a word instead of "
                            "rebuilding the decision. An option left flat is work handed "
                            "back.")
    plain = unmarked_prose(text)
    names = {m for m in BARE_IDENTIFIER.findall(plain)
             if m.lower().strip(".") not in NOT_AN_IDENTIFIER}
    for m_ in sorted(names):
        problems.append(f"identifier named in running prose ({m_!r}). Backtick anything they "
                        "could grep for, so it reads as a thing rather than as a long word.")
    for phrase in unintroduced(text):
        problems.append(f"points at something never introduced ({phrase!r}). He has not "
                        "been given it, and a definite article does not give it to them. "
                        "Name it the first time, or say what it does: 'a nightly job that "
                        "reopens closed issues' before 'the job'.")
    for pat, plain_word in JARGON_OF_ART:
        m_ = re.search(pat, plain, re.I)
        if m_:
            problems.append(f"industry term where an ordinary word does ({m_.group(0)!r}). "
                            f"Say {plain_word}. A name they can search for is not jargon and "
                            "stays; a term of art has nothing behind it to find.")

    if prose and not NEXT_STEP.search(prose[-1]):
        problems.append("final paragraph does not state a next step "
                        "(it must contain 'Next step')")

    # Length. URLs are proof, not prose, so they do not count toward it.
    def wc(s):
        # Markup is not prose. Before the mark wrapper this cost nothing; with it
        # the style attribute alone would eat a dozen words of a 150-word budget.
        return len(TAG.sub(" ", URL.sub("", s)).split())

    per = [wc(p) for p in prose]
    total = sum(per)
    if total > max_total_words:
        problems.append(f"{total} words, cap is {max_total_words} "
                        f"(per paragraph: {per}). URLs are not counted. "
                        "Cut claims and move them to your notes; do not cut "
                        "connective tissue.")
    for i, n_ in enumerate(per, 1):
        if n_ > max_para_words:
            problems.append(f"paragraph {i} is {n_} words, cap is {max_para_words}")
    for i, para in enumerate(prose, 1):
        stripped = URL.sub("", para)
        sents = [s for s in SENT.split(TAG.sub(" ", stripped)) if s.strip()]
        if len(sents) > MAX_PARA_SENTENCES:
            problems.append(f"paragraph {i} has {len(sents)} sentences, "
                            f"cap is {MAX_PARA_SENTENCES}")
        for s in sents:
            if len(s.split()) > MAX_SENTENCE_WORDS:
                problems.append(f"a sentence in paragraph {i} runs "
                                f"{len(s.split())} words, cap is {MAX_SENTENCE_WORDS}")
                break

    return problems
