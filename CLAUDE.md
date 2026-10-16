# CLAUDE.md

Administration scripts for the Berkeley Moshav community: Gather groups,
their Google Drive folders, the Google Groups used as their email lists, and
the Circle Structure wiki page, plus two Apps Script projects. README.md
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

Scripts in `data_import/`, `google_setup/`, and `cleanup/` were one-offs and
are described in the past tense; describe current scripts in the present
tense.

## Tests

Run `python -m pytest` before committing. `tests/test_gather_groups.py` and
`tests/test_gather_photos.py` currently fail because they import modules that
were since renamed; any other failure is new.
