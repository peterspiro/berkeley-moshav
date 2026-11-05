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

## Apps Script files

In each `.gs` file outside `archive/`, keep the top-level functions in
alphabetical order (ignoring case), each with its comment just above it,
after the file's header comment and its constants: the Apps Script
editor's function menu lists functions in file order, and that's how the
functions to run by hand are found. Put a new function in its place, and
move one that's renamed. `tests/test_gs_function_order.py` checks this.

## Tests

Run `python -m pytest` before committing; all tests should pass.
