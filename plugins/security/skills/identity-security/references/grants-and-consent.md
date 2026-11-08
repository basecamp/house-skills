# Grants and consent

An app grant, an API token, an app password or a delegate is a second way into the account that
outlives the session that made it. Creating one is durable control; a read-scoped one is
exfiltration ([account-access-design.md](account-access-design.md)).

## Consent

- **The person sees who is asking and for what**, in words, before granting: the app's verified name,
  the scopes as things it can do ("read and send your email"), and how long it lasts.
- **No grant without a consent screen**, except for a client whose identity and redirect the server
  has verified as first-party. Consent skipped for an unverified client hands the
  grant to whoever can claim its redirect.
- **Consent can be phished.** A consent screen reached from an attacker's link, or a device-code
  prompt the attacker started, asks the owner to approve the attacker. Show where the request came
  from, and treat a grant made this way like any other durable change: a notice and a place to end it.

## Client identity

- **A redirect is identity.** Register exact redirect URIs and compare them exactly, by scheme, host,
  port and path, never by prefix or contains.
- **Native apps use claimed links or loopback redirects with PKCE**, not a custom scheme another app
  can register.
- **Nothing presents itself as first-party unless it is.** Names, logos and wording on the consent
  screen come from what the server verified, not from what the client sent.

## Least privilege and expiry

- **Ask for the narrowest scope that does the job**, and let the person grant less where the app can
  work with less.
- **Grants expire or are reviewed.** A refresh token that lives forever is a permanent second
  password. Expire unused grants, rotate refresh tokens, and sender-constrain them where the client
  can.
- **The owner sees and ends every grant** in the same list as their sessions
  ([sessions-and-step-up.md](sessions-and-step-up.md)), and a durable change (a password reset, a
  recovery) decides deliberately which grants it ends.

## Delegates

A delegate holds shared authority for the work it was given. It never extends to the owner's own
factors, recovery methods, grants or delegates, and adding one is a durable change with a notice.
