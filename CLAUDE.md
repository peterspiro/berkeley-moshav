# CLAUDE.md

Administration scripts for the Berkeley Moshav community: Gather groups,
their Google Drive folders, the Google Groups used as their email lists, and
the Circle Structure (a Google Sheet and Doc kept by an Apps Script), plus
three Apps Script projects. README.md
describes the layout and every script — read it first.

## Keep README.md up to date

When you add, remove, or rename a script, or significantly change what one
does (its purpose, its main steps, or its options), update README.md in the
same commit:

- the script's description under "Scripts",
- the "Directories" table, if you add a directory or change what one is for,
- the intro or "How the group linking fits together", if the change affects
  how the pieces fit together.

`tests/test_readme.py` fails if a script is missing from README.md or
README.md names a script that no longer exists. It can't tell whether a
description is still accurate, so check that yourself.

Scripts in `archive/` were one-offs that are no longer used, and are
described in the past tense; describe current scripts in the present tense.
Current scripts must not import from `archive/`; put shared code in `util/`.

## Keep real data out of the repo

The repo is public. Nothing in it, or in a commit message, should identify
a real person or give a scraper something to collect:

- **People.** No real member's, vendor's, or other person's name, email
  address, phone number, or unit. Make names up (Alex Green, Dana Smith),
  and don't copy rows from the roster sheet, Gather, or Airtable into
  tests, examples, or docs, even to reproduce a bug: make up a case with
  the same shape (an accent, a nickname, a hyphen). Give made-up people
  `example.org` or `.example` addresses and `555-01xx` phone numbers.
- **Organisational addresses.** Never write one out whole. In docs and
  comments write the name and `@` (`admin@`, `all.members@`; `berkeley@` is
  at urbanmoshav.org). In code, put it together from its name and `DOMAIN`
  (or `URBAN_MOSHAV_DOMAIN`): `f"admin@{DOMAIN}"`, `` `admin@${DOMAIN}` ``.
  In tests, use an `example.org` address, or import the constant where the
  code checks for it. `tests/test_no_whole_addresses.py` checks this. Text
  that's copied out for members (the admins' guide's Google Doc, a drafted
  email) gets its whole addresses when it's copied, not in the repo.
- **Secrets and IDs.** No credentials or tokens (see `.gitignore`), and no
  Airtable base, table, or token IDs: those are Script Properties or read
  at run time.

Check for these before committing, and in what comes in when merging or
pulling `main`: look through the diff for names, addresses, phone numbers,
and IDs, fix what you find, and tell the user.

## Apps Script files

In each `.gs` file outside `archive/`, keep the top-level functions in
alphabetical order (ignoring case), each with its comment just above it,
after the file's header comment and its constants: the Apps Script
editor's function menu lists functions in file order, and that's how the
functions to run by hand are found. Put a new function in its place, and
move one that's renamed. `tests/test_gs_function_order.py` checks this.

## Gather wiki pages

In a file that becomes a Gather wiki page:

- Put each paragraph and list item on one line: Gather's Markdown keeps
  line breaks (GitHub's doesn't).
- Leave out the page's title: Gather's wiki editor has its own field for it.
- Put text to fill in on the page in single brackets (`[phone number]`):
  Gather makes double brackets a link to a new wiki page.

## Tests

Run `python -m pytest` before committing; all tests should pass.
