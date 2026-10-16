# archive

One-off scripts and plans that are no longer in use, kept for reference:

- `data_import/`: scripts that were used to populate Gather from the
  community's spreadsheets and website
- `google_setup/`: scripts that were used to set up the Google Groups and
  Drive links for the existing groups
- `cleanup/`: scripts that were used to undo earlier approaches
- `gather-sandbox-plan.md`, `gather-import-plan.md`: plans for the local
  Gather test instance and the member import

The repo's main [README](../README.md) describes each script. They still run
(from the repo root, e.g. `python archive/data_import/parse_households.py`),
but current scripts must not import from here; shared code belongs in
`util/`.
