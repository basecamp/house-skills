# Recurring identity-security families

Shapes that recur across reports, audits and ports of account flows. Each is one design decision that
shows up as many variants, so check the decision, then every route that reaches it. A question here is
a prompt to look, not a finding: judge what you find by the decision record in SKILL.md.

| Family | The question to ask |
|---|---|
| **A path skips the factor** | Does every way to start a session (web, API, each native app, OAuth or SSO, a recovery code, an app password) require the factor the account has? Is a recovery code spent on use? |
| **Recovery weaker than sign-in** | Does a recovery or new-device path accept less than sign-in does, such as an emailed link that resets the password and the factor together? ([recovery.md](recovery.md)) |
| **A gate on one route, not its siblings** | The move is gated on the settings page. Is it gated on the API endpoint, the JSON variant, the mobile screen, the bulk action that does the same thing? |
| **Checked then, acted on later** | Is the step-up, entitlement or ownership still true when the effect happens, or was it photographed into a flag, a job argument or a signed token at check time? |
| **A session that survives a privilege change** | Is the session identifier replaced at sign-in, step-up and role change, or can one minted earlier ride along? |
| **A token that names the object, not the actor** | A signed link or capability says *what* to act on. Does it also bind *who* may redeem it, or does the redeeming session supply the actor? |
| **A token that outlives the state it encoded** | An invitation, reset or verification link minted for one address or state. Is it refused once that state changed? |
| **Credentials that survive the change meant to end them** | After a password reset, factor removal, sign-out or account closure, which sessions, grants and tokens still work, and is that the decision you'd defend? ([sessions-and-step-up.md](sessions-and-step-up.md)) |
| **A binding with a bearer fallback** | Does a route that should require a sender-constrained proof also accept the bare token? |
| **A downgrade re-opens it** | Is there a legacy, compatibility or fallback path that skips the stricter one? Does a refactor or protocol change near an old fix keep the property it established? |
| **A notice the attacker reads** | Does the security notice go to a channel the attacker holds, such as the account's own mailbox or a device they enrolled? |
| **Trust decided on a URL string** | Is a redirect target, callback or origin compared as text (prefix, suffix, contains) rather than parsed and compared by scheme, host and port? |
| **A callback anyone can claim** | Does an OAuth redirect use a custom scheme or unverified link another app can register? Is consent skipped for a client whose redirect isn't verified? |
| **A credential carried somewhere it shouldn't go** | Does a client follow a cross-host redirect with a cookie or token attached? Does a deep link or launch intent load an untrusted page with a signed-in context? |
| **Cross-site requests on security flows** | Can another site trigger a factor change, app grant, forwarding rule or sign-in into the attacker's account (login CSRF)? |
| **A deceptive or over-broad app grant** | Can a third-party app present itself as first-party, ask for more than it needs, or get a grant with no consent screen? ([grants-and-consent.md](grants-and-consent.md)) |
| **Guessing without limits, or limits that lock out** | Are codes and passwords slowed per guesser and across accounts, and does the owner keep a way in while an attacker is guessing? |
| **Lockout by design** | Lost phone, lost codes, an unreachable backup address, a forgotten enrolment, a recovery that needs the factor it recovers. Which of these ends in a support ticket? |

## Often dismissed: the question that decides

These are often reported and often not worth a change on their own. None is exempt; each turns on a
question about evidence.

| Reported | Worth acting on when |
|---|---|
| Account enumeration | It works at volume and feeds stuffing or targeted phishing, or reveals that a person has an account where that is itself sensitive. |
| Missing rate limiting | Low-rate guessing across many accounts succeeds, or one account's codes or passwords can be guessed in their lifetime. Rate limits are the main move against attackers at scale. |
| Missing headers or cookie flags | The cookie carries a session, a step-up state or a recovery flow. |
| Clickjacking or CSRF | The page holds a move that grants durable control, locks out or exfiltrates. |
| An open redirect | It can stand in for an OAuth redirect or carry a token-bearing link. |
| Self-exploitation | The victim can be deceived into delivering it: an import, a paste, a consent screen, a shared link. |
| Needs a rooted device or physical access | The attacker you named for this client is the device in the wrong hands, including a shared or family device. |
| "Not asked to re-authenticate" | The move grants durable control, locks out or exfiltrates, and the attacker you named lacks what step-up would ask for. |
