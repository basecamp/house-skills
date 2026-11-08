# Account access design

The core of the lens: who the attacker is, what is worth guarding, and the order of moves.

## Name the attacker by the credential the client holds

| Credential the client holds | What a stolen copy is worth | The attacker to design for |
|---|---|---|
| Sender-constrained token (DPoP, mutual TLS, a device-bound session) | Nothing without the private key, if the server checks the proof on every route that accepts the token and no bearer fallback exists | The device in the wrong hands: unlocked and unattended, malware using the key in place, coercion. Also whoever can export the key, unless its storage forbids that |
| Bearer cookie or token | Replay from anywhere until it expires or is revoked | Anyone holding a copy: cookie-stealing malware, a leaked log or backup, a shared computer |
| No session yet: a password, an emailed link, a recovery code | A new session | Phishing, credential stuffing, a compromised mailbox, abuse of recovery |

Prove the property; don't infer it from the label:

- **A key is non-exportable only if its storage is.** A hardware-backed, non-exportable key differs
  from a software key in app storage, which malware or a backup can copy. Either way, malware on the
  device can use the key in place, so binding narrows the attacker to the device; it doesn't remove
  them.
- **Binding holds only where it is enforced.** Check proof replay protection (nonces, freshness),
  every endpoint that accepts the token, and any route that still takes the bare token.
- **Synced passkeys are phishing-resistant, not device-bound.** A synced passkey follows the platform
  account it syncs through, so its strength includes that account's recovery.

Then ask what that attacker already holds. A control the attacker passes with what they have (a live
session, an unlocked device) stops a different attacker or none. Say which.

### Scale and targeted are different attackers

- **At scale**: credential stuffing, enumeration, low-rate guessing spread across many accounts. Judged
  by volume. Limits per source and across accounts, breached-password checks and uniform responses
  matter here, even where each attempt alone looks harmless.
- **Targeted**: the unattended device, coercion, an ex-partner, a delegate turned hostile, a phish
  aimed at one person. Judged per account, against what that person holds.

### Shared authority covers the work, not the account

A delegate, a shared mailbox's members, a team or family member, and a shared family device are not
"trusted by design". Shared authority reaches the work it was shared for (reading, sending, editing),
never the owner's own durable controls and recovery. A signed-in shared device is the device in the
wrong hands. Impact counts across an authority boundary, not across code that already holds that
authority.

## Guard what turns a session into more than a session

A session already reads and works. Guard the moves that outlast it or take it from the owner:

- **Durable control:** a new or changed second factor or passkey, password, sign-in address, recovery
  method, linked identity provider, app or OAuth grant, API token, trusted device, delegate.
- **Lockout:** removing a factor or recovery method, revoking the owner's sessions, closing the account.
- **Exfiltration:** forwarding, exports and bulk downloads, read-scoped app grants.

Gating ordinary reading and work adds friction and stops nothing a session can't already do. Where a
session's ordinary reads already expose everything, the protection is the session's own lifetime and
binding ([sessions-and-step-up.md](sessions-and-step-up.md)), not a gate in front of one page.

## Order of moves

Prefer the earlier moves and combine them. Keep prevention, detection and recovery apart: a design
that only detects is not one that prevents.

1. **Prevent by design.**
   - Bind credentials to the device, so a copy is worth less elsewhere (with the caveats above).
   - Verify the destination before data goes there: a code sent to a new forwarding address or
     export target, so the attacker can't name their own without holding it.
   - Leave no side effect an attacker profits from, like signing out the owner
     ([sessions-and-step-up.md](sessions-and-step-up.md)).
2. **Detect, and let the owner recover.** An out-of-band notice with a self-serve undo that reverses
   the change and secures the account. This is detection and recovery, not elimination: the attacker
   acts until the owner reads it, and nothing undoes data already sent. The channel must be one the
   attacker can't read. Another mailbox or device isn't independent by default: if the account is the
   mailbox, a notice to it lands in front of the attacker; a backup address may forward into it; the
   "other signed-in device" may be the one the attacker enrolled.
3. **A step-up that is sufficient and usable**, on the moves above only, where the harm lands
   before the owner could read a notice, or takes the account from them
   ([sessions-and-step-up.md](sessions-and-step-up.md)).
4. **A hold, as an option** for what undo can't claw back: forwarding, bulk export, closure, a
   recovery-method change. The change waits a while, with notice, and the owner can cancel. It has
   costs: a patient attacker waits it out, the notice may land in front of the attacker, and it adds a
   pending state and a support question ("why hasn't it happened?"). Use it only where an independent
   notice and a secure cancel can prevent the harm, and put every path to the same effect behind it.
   Never delay protective revocation: ending sessions, revoking a grant, removing forwarding take
   effect at once.
5. **Prompting everyone, last.** Broad prompts train people to click through and cost support.

## Friction and support load are costs in the same decision

- **A control that can lock the owner out fails a zero-support promise, however secure it looks.**
  Walk the lost-phone, lost-codes, dead-backup-address and forgot-I-enrolled cases
  ([recovery.md](recovery.md)). If the only way out is a support ticket, it isn't done.
- **A limit that locks out the owner is an attacker's tool.** Slow the guesser, and leave the owner a
  way in the limit doesn't close (a passkey, an emailed link).

## Calibrate

- **Design for what the mechanism allows**, not only what a report demonstrated. A bounty scores the
  demonstration; a design has to hold at the ceiling.
- **Fix the design decision, not each input.** Variants that feed one flawed decision are one fix. Look
  for the same decision on sibling routes, clients and apps: web, API, each native app, OAuth or SSO,
  a legacy or fallback path, a background job.
- **Decline true-but-not-worth-it findings with the reason**, in the thread, so it isn't re-opened.
