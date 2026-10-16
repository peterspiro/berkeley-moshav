# Gather Bulk Member Import — Implementation Plan

## Goal

Write a Python script that reads a TSV spreadsheet of community members, groups them into households, and creates corresponding household and user records in a Gather instance (`https://berkeley-moshav.gather.coop`) via browser automation.

Gather is a Rails app with no bulk-import API. Records must be created through the web UI. The source code is at `https://github.com/gather-community/gather` (branch: `develop`).

---

## Phase 1: Set Up a Local Gather Test Server

Before writing any automation, stand up a local Gather instance to test against.

### 1.1 Prerequisites

- Docker and Docker Compose (for PostgreSQL, Redis, Elasticsearch)
- `mise` tool version manager (`brew install mise` on macOS, or `curl https://mise.jdx.dev/install.sh | sh`)

### 1.2 Steps

```bash
git clone https://github.com/gather-community/gather.git
cd gather
mise deps      # Installs Ruby, Node, etc.
mise conf      # Writes config files
mise data      # Creates DB, seeds data, creates an admin user — note the credentials printed
mise ssl       # Sets up local SSL certs for gatherdev.org
bin/rails server
```

Visit `https://gatherdev.org:3000` and sign in with the admin credentials from `mise data`.

### 1.3 Manual Smoke Test

Once the server is running, manually create one household and one user through the web UI. While doing so, open browser DevTools → Network tab and capture:

- The POST request URL and payload for household creation
- The POST request URL and payload for user creation
- How the CSRF `authenticity_token` is embedded (hidden form field)
- Whether household selection on the user form is a dropdown, autocomplete, or nested-attributes inline form

Save these captures — they'll be needed to verify the automation script's form submissions.

---

## Phase 2: Spreadsheet Preprocessing

### 2.1 Input Format

The input is a TSV file with these columns:

| Column                  | Maps to                        |
|-------------------------|--------------------------------|
| `First Name`            | `user.first_name`              |
| `Last Name`             | `user.last_name`               |
| `Phone`                 | `user.mobile_phone`            |
| `Email Address`         | `user.email`                   |
| `Current Location`      | (informational only)           |
| `Local?`                | (informational only)           |
| `Kids`                  | Child users (`user.child=true`)|
| `Others in the Household` | Used to group into households |
| `Status`                | (informational only)           |
| `Unit #`                | `household.unit_num`           |
| `Link to Bio`           | (informational only)           |

### 2.2 Household Grouping Algorithm

Members cross-reference each other via the `Others in the Household` column (a comma-separated list of full names). The script must resolve these cross-references into household clusters.

```
Algorithm:
  1. Parse every row. For each row, build a set containing the row's
     own "First Name Last Name" plus every name in "Others in the Household".
  2. Use union-find (disjoint sets) to merge any sets that share a member.
     This handles transitive references — if A references B and B references C,
     all three end up in one household.
  3. Validate: all members of a cluster should share the same Unit #.
     Flag mismatches as warnings.
  4. Derive the household name. Convention: alphabetically sorted unique last
     names joined with a hyphen, e.g. "Jones-Smith". Truncate to 32 chars
     (Gather's max).
```

### 2.3 Kids Handling

The `Kids` column contains names of children who may not have their own row in the spreadsheet. For each kid name listed:

- Create a `User` record with `child: true` and `full_access: false`
- Parse the name into first/last (assume "First Last" format)
- No email is required for non-full-access users
- Assign to the same household as the parent row

### 2.4 Output

The preprocessing step should produce a data structure like:

```python
[
    {
        "household_name": "Blue-Green",
        "unit_num": 101,
        "unit_suffix": None,
        "members": [
            {
                "first_name": "Alex",
                "last_name": "Green",
                "email": "alex@example.com",
                "phone": "510-555-0101",
                "child": False,
            },
            {
                "first_name": "Robin",
                "last_name": "Blue",
                "email": "robin@example.com",
                "phone": "510-555-0102",
                "child": False,
            },
        ],
    },
    # ...
]
```

Write this as a standalone Python module (`preprocess.py`) so it can be tested independently of the browser automation.

---

## Phase 3: Browser Automation Script

### 3.1 Technology

Use **Python 3 + Playwright** (`pip install playwright && playwright install`).

Playwright is preferred over Selenium because it has built-in auto-waiting, better handling of modern JS-heavy UIs, and simpler async API.

### 3.2 Gather Data Model (from source code)

Key facts from `app/models/household.rb` and `app/models/user.rb`:

**Household:**
- `name`: string, max 32 chars, required, unique per community
- `unit_num`: integer
- `unit_suffix`: string
- `community_id`: required (set by the app based on the logged-in context)

**User:**
- `first_name`, `last_name`: required
- `email`: required if `full_access` and `active`; must be unique
- `household_id`: required (foreign key to household)
- `mobile_phone`, `home_phone`, `work_phone`: optional
- `child`: boolean, default false
- `full_access`: boolean, default true (forced true for adults)

**Routes** (from `config/routes.rb`):
- `POST /households` — create a household
- `POST /users` — create a user
- `GET /households` — list households
- `GET /users` — list users

### 3.3 Script Structure

```
gather_directory.py
├── login(page, base_url, email, password)
├── get_csrf_token(page)
├── find_existing_household(page, name) -> id or None
├── create_household(page, base_url, household_data) -> id
├── find_existing_user(page, email) -> id or None
├── create_user(page, base_url, user_data, household_id)
└── main(tsv_path, base_url, email, password, dry_run=False)
```

### 3.4 Core Logic

```
main():
    households = preprocess(tsv_path)
    login(page, base_url, admin_email, admin_password)

    for household in households:
        # Idempotency: check if household already exists
        hh_id = find_existing_household(page, household["household_name"])
        if hh_id is None:
            hh_id = create_household(page, base_url, household)
            log("Created household", household["household_name"], hh_id)
        else:
            log("Household already exists", household["household_name"], hh_id)

        for member in household["members"]:
            # Idempotency: check if user already exists (by email for adults, by name for kids)
            existing = find_existing_user(page, member)
            if existing:
                log("User already exists, skipping", member)
                continue
            create_user(page, base_url, member, hh_id)
            log("Created user", member["first_name"], member["last_name"])
```

### 3.5 CSRF Token Handling

Rails embeds an `authenticity_token` in every form as a hidden field. The script must:

1. `GET` the "new" page (e.g., `/households/new`) to load the form
2. Extract the `authenticity_token` from the hidden input
3. Either submit the form via Playwright's `fill()` + `click()` (preferred, since it handles CSRF automatically), or include the token in a direct POST

**Recommended approach:** Use Playwright's high-level API (`page.fill()`, `page.click()`) to interact with the forms as a real user would, rather than crafting raw POST requests. This avoids CSRF and JavaScript issues entirely.

### 3.6 Form Field Discovery

The exact HTML field names need to be confirmed against the running instance (Phase 1 smoke test), but based on Rails conventions and the model code, they are expected to be:

**Household form** (`/households/new`):
- `household[name]`
- `household[unit_num]` (or `household[unit_num_and_suffix]` — the model has a combined setter)

**User form** (`/users/new`):
- `user[first_name]`
- `user[last_name]`
- `user[email]`
- `user[mobile_phone]`
- `user[household_id]` (dropdown or autocomplete to pick existing household)
- `user[child]` (checkbox)
- `user[full_access]` (checkbox, may be auto-set for adults)

### 3.7 CLI Interface

```
python gather_directory.py \
    --tsv members.tsv \
    --base-url https://gatherdev.org:3000 \
    --email admin@example.com \
    --password adminpassword \
    --dry-run        # Optional: log what would happen without submitting
```

### 3.8 Error Handling and Logging

- Log every action (household created, user created, skipped, failed) with timestamps to both stdout and a `import_log.csv`
- On failure: capture a screenshot (`page.screenshot()`), log the error, continue with the next record
- Support resumability: the idempotency checks (find existing household/user) mean the script can be re-run safely after a partial failure

### 3.9 Testing Checklist

Run against the local Gather dev server with the sample TSV:

- [ ] Alex Green and Robin Blue are grouped into one household
- [ ] Household is named "Blue-Green" (alphabetical) with unit_num 101
- [ ] Both users are created as adults with correct emails and phones
- [ ] Running the script a second time creates no duplicates
- [ ] A partial run (interrupted mid-way) can be resumed without duplicates
- [ ] Kids listed in the `Kids` column are created as child users in the correct household
- [ ] Dry-run mode produces a log but makes no changes
- [ ] Errors on individual records don't halt the entire import

---

## Files to Produce

| File                  | Purpose                                      |
|-----------------------|----------------------------------------------|
| `preprocess.py`       | TSV parsing and household grouping logic      |
| `gather_directory.py`    | Playwright browser automation script          |
| `test_preprocess.py`  | Unit tests for the preprocessing module       |
| `requirements.txt`    | `playwright`, `pytest`                        |
| `docker-compose.yml`  | (Optional) Containerized Gather test server   |

## Key Source Files in Gather (for reference)

- `app/models/user.rb` — User model, validations, associations
- `app/models/household.rb` — Household model, validations
- `config/routes.rb` — All URL routes
- `db/schema.rb` — Full database schema
