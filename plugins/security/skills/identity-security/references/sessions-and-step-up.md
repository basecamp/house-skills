# Sessions and step-up

## Sessions

- **Fixation**: issue a new session identifier at sign-in and at every change of privilege (step-up,
  a role change, a factor change, switching account). A session minted before authentication never
  becomes the authenticated one.
- **Lifetime, idle and absolute, per credential kind and client.** A bearer cookie on a shared
  computer earns a shorter life than a sender-constrained token on a phone whose key never leaves it.
  An absolute limit bounds a stolen copy no idle timer will catch, since the thief keeps it busy.
- **Rotate on privilege change and on refresh.** A refresh token that rotates, with reuse of an old
  one ending the whole family, turns a silent copy into a detected one.
- **A session list the owner can see and end**: each session, app grant and token, with device,
  approximate place and last use, and a way to end any one. This is the design-out behind "sign out
  everywhere": the owner removes the thief by name, and keeps their own sessions.
- **Revocation reaches every credential kind**: cookies, refresh and access tokens, app passwords,
  API tokens, and anything a background job or push registration holds for the session.

### Implicit revocation needs the actor to prove more than a session

"Sign out all other sessions" as a side effect of a change made from a session keeps the acting
session, maybe the thief's, and signs out the owner. Revoke implicitly only where the actor proved
something a session holder lacks: a reset through an emailed link, a recovery, a factor the session
didn't have. Otherwise offer signing out everywhere else as an explicit action beside the session
list, and judge that action against the same attacker.

## Step-up

### Sufficient and usable, not strongest available

Ask for a proof the attacker you named lacks, at the assurance the move needs, in a form the person
can complete. "Their strongest factor" is a good first offer, not a rule: a passkey or second factor
before a password, and the device's own user verification on a device-bound client, where the
attacker is the device in the wrong hands.

- **A fresh sign-in counts**, at the required assurance.
- **A biometric or passcode prompt counts only when the server verifies it.** A local prompt that
  unlocks a screen proves nothing to the server. It counts when the server checks something that
  can't be produced without user verification against a challenge it issued: a WebAuthn assertion
  with user verification, or a signature from a key whose use the platform gates on user
  authentication.
- **Keep their place**: return to the action with what they typed.
- **The API answers "step-up needed" in a form the app reads**, and the app prompts natively. Never a
  dead-end refusal.

### The window, set per credential kind

How long a fresh proof lasts, and from what event, is part of the design:

- **On a bearer client**, a window that starts at sign-in hands someone who copies a fresh cookie the
  whole window. Keep it short, anchor it to the proof itself, and for the moves that take the account
  from the owner (removing a factor, changing recovery) ask each time.
- **On a sender-constrained client**, a copy is worth little, so the window can be longer; the
  question is whether the person at the device is the owner, which the device's user verification
  answers.

### Risk-based step-up

Credible signals keep step-up rare for the owner and likely for the attacker: a new device, an old
session, a recent credential change, a sharp change of network or place, automation. Weigh what each
costs:

- **False positives** lock out travellers, people on VPNs or shared networks, and anyone whose
  device just updated. Each one is a support question.
- **Accessibility**: a timed code, a CAPTCHA or a biometric-only path excludes someone; offer another
  proof of the same strength.
- **Privacy**: device fingerprints and location are personal data you now collect and keep. Collect
  the least that decides, and treat a new collection as a change to what the product gathers.
- **A signal the attacker controls is no signal.** A user agent or a client-reported flag is a claim.
