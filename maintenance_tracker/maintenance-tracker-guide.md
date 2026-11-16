# Maintenance tracker: a guide for the Maintenance & Warranty Circle

This is how the circle uses the maintenance tracker day to day. It's for
circle members; you don't need to know how the tracker is built to use it.

## What the tracker is

The tracker is where every repair request, question, and warranty claim for
the building is recorded, from the moment a member reports it until it's
closed. It lives in Airtable, a kind of shared spreadsheet, in a base called
**Building Maintenance**.

These are the parts you'll meet:

| Part | Who uses it | What it's for |
|---|---|---|
| The request form | Members | Reporting a problem or asking how something works |
| The status page | Members | Seeing where every request stands |
| **Maintenance tracker**, an interface in Airtable, with its **Triage**, **Requests**, and **Claims** pages | The circle | Triage, working on requests, and warranty claims |
| Its **Contacts**, **Warranties**, and **Patterns** pages | The circle | Contractors and how to reach them; each warranty's terms and how to claim under it; charts of what's going wrong where |
| The "Log an update" form | The circle | Recording a call, a visit, or a note, from a phone |
| Emails | Members and the circle | The tracker sends these by itself (see below) |
| The weekly digest | The circle | A Sunday email summing up what needs attention |

The coordinator can give you the links to each. Every circle member also has
their own link to the "Log an update" form, which fills in their name; the
circle's "start here" doc lists them.

## What the tracker does by itself

You don't have to do any of these:

- **When a member sends a request**, it emails them to say it arrived, with
  the request's number.
- **If the request is an emergency or urgent**, it emails the circle's list
  straight away.
- **When a visit is scheduled or moved**, it emails the member the time.
- **When work is marked done**, it emails the member to ask whether it's
  fixed.
- **Every change of status** is added to the request's history.
- **Every Sunday**, it emails the circle the weekly digest.
- **It keeps a backup** of everything, including photos.

These emails are not instant. For now they can take **up to two hours** to
go out. So for a real emergency, don't rely on the tracker to raise the
alarm: members are told to phone.

## Who does what

- **The coordinator** looks at each new request and sorts it out. This is
  called triage. The coordinator changes from time to time.
- **The owner** of a request is the one circle member who sees it through
  to the end. Every request should have exactly one.
- **Anyone in the circle** can record what happened on a request: a call
  they made, a visit, something they noticed.

## A request's life

Each request has a **Status**. This is what each one means, and what the
member sees on the status page:

| Status | Meaning | What the member sees |
|---|---|---|
| New | Just arrived; nobody has looked at it | Received |
| Triaged | Looked at, and given an owner | Being looked into |
| Waiting on builder | The builder has to act | Waiting on the builder |
| Waiting on vendor | A contractor has to act | Waiting on a contractor |
| Waiting on volunteer | One of us has to act | Waiting on a volunteer |
| Scheduled | A visit has a date and time | Visit scheduled for … |
| Done, to confirm | The work is done; waiting for the member to agree | Done, waiting for the reporter to confirm |
| Closed | Finished | Closed |
| Duplicate | The same problem as another request | (not shown) |
| Declined | Not something the circle will handle | (not shown) |

## Triage (the coordinator)

Do this at least weekly, and the same day for anything urgent.

1. Open **Maintenance tracker** and go to its **Triage** page. It lists the requests nobody has looked at
   yet, emergencies first, then the oldest.
2. Pick a request. Read what the member wrote and look at their photos.
3. Fill in:
   - **Title**: a few words naming the thing and what's wrong, such as
     "Bath fan rattles". **This is shown on the public status page**, so
     never put a name, a health matter, or when someone is away in it.
   - **Location**, if it's empty or wrong.
   - **System**: plumbing, electrical, and so on. Correct the member's
     choice if needed.
   - **Component**: the particular fixture or piece of equipment, if it's
     in the list. Leave it empty if not.
   - **Responsibility**: the builder's warranty, a manufacturer's warranty,
     the HOA, or the unit's owner. For a warranty item you can leave it
     empty: the tracker fills it in from the Warranty, within two hours.
   - **Warranty**: for a warranty item, which warranty covers it. The
     tracker then shows the date a claim must be made by.
   - **Owner**: the one person who will see it through.
4. Last of all, change **Status** to **Triaged**. The request then leaves
   the Triage page, which is how you know it's dealt with.

When the Triage page is empty, you're done.

**If it's the same problem as an earlier request**, set **Duplicate of** to
the earlier one and Status to **Duplicate**.

**If it isn't the circle's to handle**, set Status to **Declined**, and
write to the member yourself to say why and where to turn. The tracker
doesn't email them about this.

**If the member ticked "It's in my unit"**, the tracker fills in their unit
as the location. If Location is still empty, set it yourself.

## Working on a request (the owner)

Open **Maintenance tracker** and go to its **Requests** page. It shows every request
that isn't finished,
grouped by owner.

### Scheduling a visit

Fill in **Visit** with the date and time. That's all: the tracker changes
the Status to Scheduled and emails the member the time. If the visit moves,
change Visit, and the member is told the new time.

The email tells the member either that nobody needs to be home (if they
said we may enter) or to make sure someone is.

### Recording what happens

Each call, email, and visit should be recorded as an **update**. Together
they are the request's history, and for a warranty item they are our
evidence if the builder disputes a claim. Record facts: who you spoke to,
what was said, what was done. Leave out guesses about whose fault it is.

There are two ways to add an update:

- On the **Requests** page, under the request's **Updates**. You needn't say
  who you are: the tracker knows from your Airtable sign-in, and fills it
  in.
- With the **"Log an update"** form, which is easier on a phone. Use your
  own link to it, from the circle's "start here" doc, so that your name is
  filled in.

**"Tell the reporter"**: tick this box on an update and the tracker emails
your words to the member, exactly as you wrote them. Use it to keep them
informed ("The plumber is booked for next week"). Leave it unticked for
notes meant only for the circle.

### Waiting on someone

When the next step is someone else's, set Status to **Waiting on builder**,
**Waiting on vendor**, or **Waiting on volunteer**, so anyone can see why
nothing is happening.

### Finishing

1. When the work is done, set Status to **Done, to confirm**. The tracker
   emails the member to ask whether it's fixed.
2. If they say it isn't, set the Status back to whatever fits, and carry on.
3. If they say it is, or a week passes with no reply, set Status to
   **Closed**. You can write what fixed it in **Resolution**.

## Dealing with contractors

Each contractor and the builder should hear from **one** of us, not from
whoever happens to be handling a request. Before you contact one, check
who our contact for them is, and go through that person. Their names and
numbers are on the **Contacts** page, where you can also add a contractor
or correct one's details.

## Warranty claims

A warranty item becomes a **claim** when we formally tell the builder (or a
manufacturer) about it in writing. Claims are made and followed on the
**Claims** page of Maintenance tracker, which lists them by status,
oldest first, and shows each claim's requests and its updates. Log each email and call
about a claim as an update on it, as you would on a request.

1. **Create the claim**: add one, give it a short Title, and link it to
   its **Requests**, its **Warranty**, and its **Warrantor** (who it's
   addressed to). You choose the Warranty and the Warrantor yourself, and
   the tracker doesn't check them against each other or against the
   requests. Usually the Warrantor is whoever gave the warranty, but not
   always: a manufacturer's warranty may be claimed through the builder.
2. **One problem in several places** should be one claim, not several. Link
   all the requests to it and set **Scope** to **Systemic**. This asks the
   builder to fix it throughout the building, not unit by unit.
3. **Read the Draft.** The tracker writes the letter for you in the claim's
   **Draft** field, listing each request. Check it reads correctly; it uses
   the titles written at triage.
4. **Send it**: copy the Draft into an email from the circle's address to
   the person the builder has named for claims, and attach the requests'
   photos. Always in writing, always from the same address, always with
   the claim's number in the subject.
5. **Record that you sent it**: set the claim's Status to **Submitted**, and
   fill in **Submitted on**, **Submitted how**, and **Sent to**. Save a copy
   of the sent email in the claim's **Folder**.
6. **Record the builder's answers** as they come: **Acknowledged on**,
   **Response**, **Promised by**, **Work done on**.
7. **When the circle is satisfied with the repair**, fill in **Accepted by**
   and **Accepted on**, and set Status to **Accepted**.

**Claim deadline** is the date by which a claim must be made. It's worked
out from the warranty and shown on each request and each claim. A warranty
item with no claim made is listed in the weekly digest as its deadline gets
near. Don't let one pass: after the deadline the builder may not have to
fix it.

A claim that's still being drafted doesn't count as made. Only sending it
does.

## The weekly digest

Every Sunday the circle's list gets an email listing what needs someone.
Each section appears only when it has something in it:

| Section | What to do |
|---|---|
| New this week | Nothing; for information |
| To triage | The coordinator triages them |
| No owner | Give each one an owner |
| Scheduled, but with no visit time | Fill in Visit, or change the Status |
| Visit has passed | Mark it done, or give it a new time |
| Done, and waiting to be confirmed | Close them, unless the member said it isn't fixed |
| Gone quiet | Nothing has happened for two weeks: the owner should chase it |
| Warranty deadline within 90 days, and no claim made | Make the claim |
| Claims the builder hasn't acknowledged | Chase the builder |
| Claims past the date the builder promised | Chase the builder |
| Repeats | The same thing is going wrong in several places: consider one systemic claim |

"Repeats" only works if triage is done carefully, since it counts requests
by their System, Room, and Component.

## Things to be careful about

- **Don't delete a request, an update, or a claim.** If one is a mistake or
  a duplicate, mark it Duplicate or Declined instead. The record matters
  more than tidiness.
- **An update can't be changed once it's saved**, on the Requests page or
  the Claims page. If one is wrong, add another that corrects it ("The
  visit was Tuesday, not Monday"), so the record shows both. If one is on
  the wrong request, or says something that shouldn't have been written
  down, ask whoever looks after the tracker.
- **If something disappears**, it has probably just moved: a request leaves
  the Triage page as soon as its Status changes. Look on the Requests
  page. If a row really was deleted, tell whoever looks after the tracker
  soon; it can be recovered for about a week.
- **Leave these fields alone**: Acknowledged on, Status noted, Told
  reporter on, Member-visible status, and anything in the People and
  Locations tables. The tracker fills them in, and they're overwritten.
- **Titles are public.** So are the status and location of each request.
  Descriptions, photos, names, and updates are not.
- **Don't add people, locations, warranties, or warrantors** while working
  on a request or a claim: choose them from the lists. A missing
  **Warrantor** is added on the **Contacts** page. For anyone or anything
  else that's missing from a list, ask whoever looks after the tracker;
  the **Warranties** page shows the warranties but can't change them.
- **Advice from our attorney doesn't go in the tracker.**

## Who to ask

For how the circle works, ask the coordinator. For the tracker itself (a
missing field, a wrong email, something that won't save), ask the
Technology Circle.
