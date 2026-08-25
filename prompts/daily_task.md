# Today's run

Find {PROSPECTS_PER_DAY} New Zealand trade and contracting businesses whose web
presence is weak or absent, and leave a personalised website-offer draft in Zoho
Mail for each one, following your standing instructions.

## Slate

Read `niches/rotation.md` in memory first, then pick today's mix from below —
no more than two prospects from any one trade, spread across at least three of
the listed regions, favouring trades you have not used recently.

**Trades**
{NICHES}

**Regions**
{REGIONS}

## Sender identity

The email template in your standing instructions carries its own sign-off, and
that sign-off is authoritative — reproduce it exactly as written and do not
substitute the values below into the body.

These are for the sending account and for reference only:

- Reply-to / fromAddress: {SENDER_EMAIL}
- Company: {SENDER_COMPANY}
- Website: {SENDER_WEBSITE}

Append this opt-out line as the last line of every email, after the template's
sign-off, separated by a blank line — the template does not include it:

> {UNSUBSCRIBE_LINE}

## Definition of done

- {PROSPECTS_PER_DAY} businesses researched, qualified as having no website or a
  weak one, and deduplicated by grepping `contacted/index/` and
  `excluded/index.md` — before researching, not after.
- Anyone who already has a good, current site excluded rather than emailed.
- A Zoho Mail **draft** created for each (never sent).
- An index line appended to `contacted/index/<YYYY-MM>.md` and a detail file
  written to `contacted/detail/<YYYY-MM>/<domain>.md` for each, immediately
  after its draft is created — plus a line in `excluded/index.md` for anything
  you rejected.
- `niches/rotation.md` updated with today's date, trades and regions, and
  trimmed to the last 60 days.
- `prospects-<date>.csv` and `drafts-<date>.md` written to
  `/mnt/session/outputs/`.
- A short summary: count, trades, regions, how many were skipped for already
  having a good site, and anything that failed.
