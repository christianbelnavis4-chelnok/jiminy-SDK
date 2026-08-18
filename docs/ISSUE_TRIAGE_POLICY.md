# Issue Triage Automation - Response Scope and Tone

This defines what the issue-triage automation (`.github/workflows/triage.yml`) is allowed to do.

## Approved response scope

**Can answer directly, in a drafted (not auto-sent) response:**

- Installation/usage questions answerable from the README, docs, or code
  comments already in the repo
- "Is X supported" questions where the answer is a clear yes/no from
  existing docs
- Pointing someone to the right doc, example, or existing issue/discussion
- Basic bug triage: reproducing from a stack trace, confirming a version
  mismatch, asking for missing repro details

**Must be flagged for human review, never auto-answered:**

- Pricing, tiers, or roadmap/future features - no commitments, no "yes
  we're planning that"
- Data retention, data boundary, or security posture - these have
  precise, previously-corrected language (e.g. retention windows under
  specific tiers, HMAC-SHA256 described as tamper-evident, not
  cryptographically verified) and a wrong paraphrase here is worse than no
  answer
- Competitive comparisons ("how is this different from X") - hold for a
  human, don't improvise positioning
- Anything that reads as a security vulnerability report - route to a
  private channel, never discuss specifics in a public issue thread even
  to say "looking into it"

**Tone:** match the existing README voice - direct, unhedged, no
marketing language, comfortable saying "not yet" or "no" plainly rather
than softening. No exclamation marks, no "happy to help!" filler.

## Issue triage: PRIMARY / FALLBACK / PASS

### PRIMARY

On a new issue or PR, the automation:

1. Applies labels (`bug`, `question`, `feature-request`, `docs`) based on
   content
2. Drafts a response per the approved scope above
3. Posts the draft as a comment marked `[draft - awaiting maintainer
   review]` - never presented as a final answer

### FALLBACK

If the issue touches any flagged topic above (pricing, data
boundary/security, competitive positioning, vulnerability reports):

1. Apply a `needs-maintainer` label instead of drafting a response
2. No comment posted - silence is safer than a wrong or premature answer
   on these topics

### PASS rule (before removing the `[draft]` review step)

- Must not reference any number, date, or claim not directly present in
  README/docs at the time of triage - no inferring or estimating
- Must not be the second automated reply in the same thread - one draft
  per issue, then hand to a human
- Duplicate-issue auto-close only fires above a high title/body
  similarity threshold against an existing closed issue; anything less
  than clearly identical stays open for a human to confirm

Keep the `[draft]` gate on indefinitely until a maintainer has reviewed at
least a few weeks of drafts and trusts the pattern - don't switch to
auto-post as a default.
