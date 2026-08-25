# Example drafts

Three worked examples of what the agent leaves in the mailbox each morning.

**Every business below is invented.** The names, domains and email addresses use
RFC 2606 reserved domains (`example.com`, `.example`) and do not resolve. They
are here to show the shape of the output, not to be contacted.

The only parts that differ between these three are the greeting and the
observation paragraph. Everything else is byte-for-byte identical, which is the
point of working from a template.

---

## 1. No website — found via a trade association directory

**To:** `office@hargreavesroofing.example`
**Subject:** A website for your trade business?

```text
Hello Dave,

I'm Alex Morgan from Tradie Web Co. We build websites for trade and contracting
businesses — plumbers, sparkies, builders, roofers, landscapers — so customers
can find you, see the work you've done, and get a quote request in front of you
without playing phone tag.

I went looking for a website for Hargreaves Roofing and couldn't find one — you
come up on the Roofing Association member list and on Facebook, and those two
are doing all the work at the moment.

A typical site takes about two weeks and covers the services you offer, a photo
gallery of finished jobs, your reviews, your service area, and a quote form that
lands straight in your inbox. We sort the domain, the hosting and your Google
listing, and it looks right on a phone, because that's where your customers will
open it.

If you'd like to see what yours would look like, just reply to this email and
I'll put together a free one-page mock-up for you — no charge, no obligation.

Thanks,
Alex Morgan
Tradie Web Co
https://www.tradiewebco.example

If you'd rather not hear from me again, just reply with "no thanks" and I'll
remove you from my list.
```

---

## 2. Weak site — no mobile layout

**To:** `info@kaimaiplumbing.example`
**Subject:** A website for your trade business?

```text
Hello,

I'm Alex Morgan from Tradie Web Co. We build websites for trade and contracting
businesses — plumbers, sparkies, builders, roofers, landscapers — so customers
can find you, see the work you've done, and get a quote request in front of you
without playing phone tag.

I had a look at kaimaiplumbing.example — everything's on there, but it doesn't
resize for a phone screen, so the contact number ends up needing a pinch and a
squint to read.

A typical site takes about two weeks and covers the services you offer, a photo
gallery of finished jobs, your reviews, your service area, and a quote form that
lands straight in your inbox. We sort the domain, the hosting and your Google
listing, and it looks right on a phone, because that's where your customers will
open it.

If you'd like to see what yours would look like, just reply to this email and
I'll put together a free one-page mock-up for you — no charge, no obligation.

Thanks,
Alex Morgan
Tradie Web Co
https://www.tradiewebco.example

If you'd rather not hear from me again, just reply with "no thanks" and I'll
remove you from my list.
```

---

## 3. Weak site — free-host subdomain, gallery empty

**To:** `quotes@southernfencing.example`
**Subject:** A website for your trade business?

```text
Hello Marie,

I'm Alex Morgan from Tradie Web Co. We build websites for trade and contracting
businesses — plumbers, sparkies, builders, roofers, landscapers — so customers
can find you, see the work you've done, and get a quote request in front of you
without playing phone tag.

Your page is on a free builder subdomain and the "Our Work" gallery is still
empty — which is a shame, because the fencing photos on your Facebook page are
the best argument you've got.

A typical site takes about two weeks and covers the services you offer, a photo
gallery of finished jobs, your reviews, your service area, and a quote form that
lands straight in your inbox. We sort the domain, the hosting and your Google
listing, and it looks right on a phone, because that's where your customers will
open it.

If you'd like to see what yours would look like, just reply to this email and
I'll put together a free one-page mock-up for you — no charge, no obligation.

Thanks,
Alex Morgan
Tradie Web Co
https://www.tradiewebco.example

If you'd rather not hear from me again, just reply with "no thanks" and I'll
remove you from my list.
```

---

## What a skipped prospect looks like

Not every candidate becomes a draft. A business with a good, current site is
recorded and never contacted:

```text
mcbridebuilders.example | 2026-08-25 | already has a good website
tepuke-electrical.example | 2026-08-25 | no published email address
oakleyjoinery.example | 2026-08-25 | "no marketing enquiries please" on contact page
```

Those lines go to `excluded/index.md` in the memory store and are permanent, so
tomorrow's run doesn't spend research budget re-deciding the same thing.
