# Recovery

An account is as strong as its weakest way in, and the weakest is usually recovery. Device binding,
passkeys and second factors move the perimeter to enrolment and recovery; under a zero-support
promise, self-serve recovery *is* the support path, so it is also the attacker's.

## Map the lifecycle

For each credential and client, trace every step, and every route that reaches it:

1. **Enrolment**: how a factor, passkey, device or recovery method is added, and what proof that took.
2. **Authentication**: each way to start a session.
3. **Refresh**: how a session or token is renewed, and whether renewal re-checks anything.
4. **Recovery**: each way back in after a factor, device or password is lost.
5. **Replacement**: enrolling a new device or factor in place of a lost one.
6. **Revocation**: removing a lost device, factor or grant, from where, and what it ends.

A gap anywhere is a gap in the account. A strong step 2 behind a weak step 4 is a weak account.

## Judge each recovery path as a sign-in

A recovery path starts a session, so it faces the same attacker
([account-access-design.md](account-access-design.md)). Ask what it accepts, and whether that proof
is as strong as the sign-in it stands in for:

- **An email fallback mustn't silently defeat the second factor.** If a link to the sign-in address
  resets the password and the factor together, the account's strength is that mailbox's, whatever the
  settings page says. Where the account is itself the mailbox, a link to it proves nothing a
  session holder lacks.
- **Recovery that needs the factor it recovers is lockout**, not security.
- **A recovery code is a password with no second factor**: spent on use, rate limited, and its use
  notified.
- **A support-led recovery is a sign-in by social engineering.** Whatever a person on support accepts
  is a factor; name it and judge it as one.
- **Changing a recovery method is durable control.** Guard it as a factor change: step-up, a notice to
  the old method, and a hold as an option ([account-access-design.md](account-access-design.md)).
- **After recovery, revoke what the recovered credential could have leaked:** other sessions and tokens
  issued under the lost factor. Here implicit revocation is right, because the actor proved something
  a session holder lacks.

## Choose recovery at enrolment

- **Have people choose their recovery methods when they enrol**, not when they need them: a second
  passkey on another device, recovery codes saved somewhere they'll find, an independent backup
  address. Prompt again when they drop to one way in.
- **Independence is a property to check, not a label.** A backup address that forwards into the
  account, a second device on the same platform account, or codes stored in the account's own notes
  share a failure with what they back up.
- **Walk the losses**: lost phone, lost codes, dead backup address, forgot-I-enrolled, a recovery that
  needs the factor it recovers. Each should end in a self-serve way back, not a ticket.

## State the residual risk

No-support cannot promise recovery after every independent proof is lost. Don't weaken recovery to
make that promise true; a recovery anyone can pass makes the account anyone's. Say plainly, at
enrolment and in the decision record, what happens when every method is gone, and what the product
does then.
