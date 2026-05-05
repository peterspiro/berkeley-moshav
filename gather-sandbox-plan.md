# Plan: Gather Sandbox Dev Server for Playwright Testing

## Goal

Set up a local development instance of [Gather](https://github.com/gather-community/gather) — a Ruby on Rails community management platform — so that a Playwright script can interact with its web UI to add members to the community directory.

---

## 1. Understand the Target Application

Gather is a Rails app for cooperative housing communities. The member directory lives in the **People** module (models: `User`, `Household`, `MemberType`). The app uses:

- **Ruby 3.2.2** / **Rails 8.1** / **Node.js 18.12.1**
- **PostgreSQL 15**, **Redis 7**, **Elasticsearch 6.8**
- **Devise + OmniAuth** for authentication
- **Multi-tenancy** via `acts_as_tenant` (Cluster → Community → Household → User)
- **SSL in development** — serves on `https://gatherdev.org:3000`
- **Wildcard subdomains** — each community gets a subdomain like `https://mycommunity.gatherdev.org:3000`

The app already has a Docker Compose file for its data services and a VS Code Dev Container for a fully containerized dev experience.

---

## 2. Choose the Setup Strategy

Two viable options exist. Choose based on your host OS.

### Option A: Dev Container (Recommended)

Use the project's built-in `.devcontainer` configuration with VS Code or the devcontainer CLI. This is the path of least resistance — all system dependencies (Ruby, Node, libvips, etc.) are pre-configured in the container image.

**Best for:** macOS or Linux hosts with Docker Desktop installed.

### Option B: Manual Setup on a Linux VM/Server

Clone the repo, install `mise`, and run `mise setup`. You manage the system dependencies yourself.

**Best for:** Headless servers, CI pipelines, or environments where VS Code Dev Containers aren't practical.

This plan assumes **Option A** but notes where Option B diverges.

---

## 3. Step-by-Step Setup

### Phase 1: Prerequisites

1. **Install Docker** (Docker Desktop on macOS/Windows, or Docker Engine on Linux).
2. **Install VS Code** + the **Dev Containers** extension (Option A only).
3. **Configure wildcard DNS** for `*.gatherdev.org`:
   - On macOS: install `dnsmasq` via Homebrew and point `*.gatherdev.org` → `127.0.0.1`.
   - On Linux: add entries to `/etc/hosts` for each community subdomain you'll test (e.g., `127.0.0.1 default.gatherdev.org`), or use `dnsmasq`.
   - This is required because Gather uses subdomains to identify the current community.

### Phase 2: Clone and Launch the Dev Container

```bash
git clone https://github.com/gather-community/gather.git
cd gather
git checkout develop
code .   # Open in VS Code, then "Reopen in Container"
```

For headless/CLI usage (no VS Code):
```bash
# Install devcontainer CLI: npm install -g @devcontainers/cli
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . bash
```

### Phase 3: Run Initial Setup

Inside the container:

```bash
mise setup
```

This interactive script will:
1. **Check dependencies** (Ruby, Node, libvips, etc.)
2. **Generate config files** (`config/database.yml`, `config/settings.local.yml`, SSL certs)
3. **Start data services** via Docker Compose (PostgreSQL, Redis, Elasticsearch, Mailcatcher)
4. **Provision the database** — creates schema, seeds data, and creates an admin user
5. **Install SSL certificates** for `gatherdev.org`

**Record the admin credentials** displayed at the end — you'll need them for Playwright login.

### Phase 4: Start the Application

In two separate terminals inside the container:

```bash
# Terminal 1: Rails server + JS build + type checking
bin/dev

# Terminal 2: Background job processor
bin/delayed_job run
```

Verify the app is running by visiting `https://gatherdev.org:3000` in a browser. Sign in with the admin credentials from setup.

### Phase 5: Create Test Data (Community + Users)

Using the Rails console, ensure you have a community to work with:

```bash
rails console
```

```ruby
CH.tenant(1)   # Switch to the first cluster
# Inspect existing communities:
Community.all.map { |c| [c.id, c.name, c.slug] }
```

Note the community slug — this determines the subdomain (e.g., slug `default` → `https://default.gatherdev.org:3000`). Make sure this subdomain resolves via your DNS setup from Phase 1.

---

## 4. Configure for Playwright Access

### SSL Certificate Trust

Gather's dev server uses a self-signed SSL certificate. Playwright will reject it by default. Two options:

- **Option 1 (simple):** Launch Playwright's browser context with `ignoreHTTPSErrors: true`.
- **Option 2 (thorough):** Add the Gather dev CA cert to the system trust store, then point Playwright at it via the `PLAYWRIGHT_CHROMIUM_USE_SYSTEM_CA` env var or the `--ignore-certificate-errors` Chromium flag.

Option 1 is fine for a sandbox.

### Playwright Browser Setup

```bash
# In your Playwright project directory (outside the Gather repo)
npm init -y
npm install @playwright/test
npx playwright install chromium
```

### Base Playwright Configuration

```typescript
// playwright.config.ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  use: {
    baseURL: 'https://default.gatherdev.org:3000',
    ignoreHTTPSErrors: true,  // Self-signed cert
    headless: true,
  },
  timeout: 30000,
});
```

### Authentication Helper

Gather uses Devise for auth with standard email/password sign-in. A reusable login helper:

```typescript
// helpers/auth.ts
import { Page } from '@playwright/test';

export async function login(page: Page, email: string, password: string) {
  await page.goto('/users/sign_in');
  await page.fill('input[name="user[email]"]', email);
  await page.fill('input[name="user[password]"]', password);
  await page.click('input[type="submit"]');
  await page.waitForURL('**/');  // Wait for redirect after login
}
```

---

## 5. Playwright Script: Adding Members to the Directory

The member-creation flow in Gather involves creating a **Household** and then adding **Users** to it. The web UI path is typically:

1. Navigate to the People section (e.g., `/users`)
2. Click "Create User" or "Invite" (depends on admin permissions)
3. Fill in the user form (first name, last name, email, household, etc.)
4. Submit

### Skeleton Test Script

```typescript
// tests/add-member.spec.ts
import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';

const ADMIN_EMAIL = 'admin@example.com';   // From mise setup
const ADMIN_PASSWORD = 'password';          // From mise setup

test('add a new member to the community directory', async ({ page }) => {
  // 1. Log in as admin
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  // 2. Navigate to the people/users section
  await page.goto('/users');
  await expect(page.locator('h1')).toContainText('Directory');

  // 3. Click the "Create" or "Add" button
  //    (Inspect the actual UI to find the correct selector)
  await page.click('a:has-text("Create")');

  // 4. Fill in the new member form
  //    Gather uses simple_form, so fields follow the pattern:
  //    user[first_name], user[last_name], user[email], etc.
  await page.fill('#user_first_name', 'Test');
  await page.fill('#user_last_name', 'Member');
  await page.fill('#user_email', 'testmember@example.com');

  // 5. Select or create a household (likely a Select2 dropdown)
  //    Select2 requires special interaction — open it, search, pick
  //    See CLAUDE.md for Select2 testing patterns

  // 6. Submit
  await page.click('input[type="submit"]');

  // 7. Verify the member was created
  await page.goto('/users');
  await expect(page.locator('body')).toContainText('Test Member');
});
```

### Handling Select2 Dropdowns

Gather uses Select2 v4 extensively. The dropdowns append to `document.body`, not the form. Playwright approach:

```typescript
async function select2Choose(page: Page, selectId: string, searchText: string) {
  // Open the Select2 dropdown
  await page.evaluate((id) => {
    (document.querySelector(`#${id}`) as any)
      ?.dispatchEvent(new Event('select2:open'));
  }, selectId);
  // Or use: await page.click(`#select2-${selectId}-container`);

  // Type in the search box (appended to body)
  const searchInput = page.locator('.select2-search--dropdown .select2-search__field');
  await searchInput.fill(searchText);
  await searchInput.press('Enter');

  // Click the matching result
  await page.click(`.select2-results__option:has-text("${searchText}")`);
}
```

---

## 6. Key Gotchas & Tips

1. **CSRF tokens:** Gather is a standard Rails app with CSRF protection. Playwright handles this naturally (cookies + form tokens), so no special handling needed as long as you interact via the UI.

2. **Multi-tenancy via subdomain:** Every page request must go to the correct community subdomain. If your Playwright `baseURL` is `https://default.gatherdev.org:3000`, all `page.goto('/users')` calls will resolve to that community. Switching communities means changing the subdomain.

3. **Admin permissions:** Only users with the `admin` or `super_admin` role can create new members. The setup script creates a super admin — use those credentials.

4. **Elasticsearch indexing:** Some directory search features depend on Elasticsearch. If search isn't returning results, the index may need a rebuild:
   ```ruby
   # In rails console
   User.find_each { |u| u.__elasticsearch__.index_document }
   ```

5. **Background jobs:** Some member-creation side effects (welcome emails, group memberships, billing accounts) are handled by Delayed Job. Make sure `bin/delayed_job run` is running, or those will queue silently.

6. **Database reset:** If you need a clean slate between test runs:
   ```bash
   bin/rails db:reset   # Drops, creates, migrates, seeds
   ```

---

## 7. Summary Checklist

| Step | Command / Action | Done? |
|------|-----------------|-------|
| Install Docker + VS Code | — | ☐ |
| Configure `*.gatherdev.org` DNS | dnsmasq or /etc/hosts | ☐ |
| Clone repo + open Dev Container | `git clone` → "Reopen in Container" | ☐ |
| Run setup | `mise setup` | ☐ |
| Record admin credentials | Displayed at end of setup | ☐ |
| Start app server | `bin/dev` | ☐ |
| Start background jobs | `bin/delayed_job run` | ☐ |
| Verify app loads in browser | `https://gatherdev.org:3000` | ☐ |
| Set up Playwright project | `npm install @playwright/test` | ☐ |
| Configure `ignoreHTTPSErrors` | In `playwright.config.ts` | ☐ |
| Write login helper | Devise sign-in form | ☐ |
| Inspect member-creation UI | Identify form fields + selectors | ☐ |
| Write add-member test | `tests/add-member.spec.ts` | ☐ |
| Run tests | `npx playwright test` | ☐ |
