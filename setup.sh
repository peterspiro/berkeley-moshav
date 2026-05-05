#!/usr/bin/env bash
# Setup script to install and run a Gather development instance.
# Run from any directory. Installs Gather to /home/user/gather.
set -euo pipefail

GATHER_DIR="/home/user/gather"
GATHER_REPO="https://github.com/gather-community/gather.git"

export PATH="/opt/rbenv/bin:/opt/rbenv/shims:/opt/node22/bin:$PATH"
eval "$(rbenv init -)" 2>/dev/null || true

# ── 1. System dependencies ────────────────────────────────────────────────────
echo "==> Installing libvips42 runtime..."
sudo apt-get install -y libvips42 2>&1 | grep -E "Setting up|already installed" || true

# ── 2. Clone repo ─────────────────────────────────────────────────────────────
if [[ ! -d "$GATHER_DIR" ]]; then
  echo "==> Cloning Gather..."
  git clone "$GATHER_REPO" "$GATHER_DIR"
fi
cd "$GATHER_DIR"

# Set Ruby version (uses rbenv .ruby-version file: 3.2.x)
rbenv local 3.2.6 2>/dev/null || true

# ── 3. Configuration files ────────────────────────────────────────────────────
echo "==> Writing config files..."

# database.yml — use localhost instead of docker service name
cat > config/database.yml <<'EOF'
default: &default
  adapter: postgresql
  pool: 5
  timeout: 5000
  host: localhost
  port: 5432
  username: gather
  password: gather

development:
  <<: *default
  database: gather_development

test:
  <<: *default
  database: gather_test

production:
  <<: *default
  database: gather_production
EOF

# settings.local.yml — localhost endpoints + generated secret key
SECRET=$(openssl rand -hex 64)
cat > config/settings.local.yml <<EOF
oauth:
  google:
    client_id:
    client_secret:

secret_key_base: $SECRET

email:
  from: Gather <noreply@example.coop>
  webmaster: developer@example.com

elasticsearch:
  host: localhost
  port: 9200

redis:
  url: redis://localhost:6379/0

mailman:
  api:
    base_url: http://localhost:8001
EOF

# ── 4. Fix tsconfig for newer TypeScript ─────────────────────────────────────
if ! grep -q "ignoreDeprecations" tsconfig.json; then
  sed -i 's/"moduleResolution": "node",/"moduleResolution": "node",\n    "ignoreDeprecations": "6.0",/' tsconfig.json
fi

# Fix Procfile.dev for non-interactive esbuild --watch
sed -i 's/yarn build --watch$/yarn build --watch=forever/' Procfile.dev

# ── 5. Docker services ────────────────────────────────────────────────────────
echo "==> Starting Docker daemon (if needed)..."
if ! docker info &>/dev/null; then
  sudo dockerd --host=unix:///var/run/docker.sock --iptables=false &>/dev/null &
  sleep 5
fi

echo "==> Creating Docker network..."
docker network create gather-network 2>/dev/null || true

# Use locally available elasticsearch:8.14.0 image
sed -i 's|image: docker.elastic.co/elasticsearch/elasticsearch:.*|image: elasticsearch:8.14.0|' docker-compose.yml
# Ensure security disabled for ES8
if ! grep -q "xpack.security.http.ssl.enabled" docker-compose.yml; then
  sed -i '/xpack.security.enabled=false/a\      - xpack.security.http.ssl.enabled=false' docker-compose.yml
fi

echo "==> Starting PostgreSQL, Redis, Elasticsearch..."
docker compose up -d postgres redis elasticsearch

echo "==> Waiting for services..."
until curl -sf http://localhost:9200/_cluster/health &>/dev/null && \
      pg_isready -h localhost -p 5432 -U gather &>/dev/null && \
      redis-cli ping &>/dev/null; do
  sleep 3
done
echo "    Services ready."

# ── 6. /etc/hosts ─────────────────────────────────────────────────────────────
grep -q "gatherdev.org" /etc/hosts || echo "127.0.0.1 gatherdev.org" | sudo tee -a /etc/hosts

# ── 7. Ruby gems + JS packages ────────────────────────────────────────────────
echo "==> Installing Ruby gems..."
bundle install

echo "==> Installing JS packages..."
yarn install

# ── 8. Database ───────────────────────────────────────────────────────────────
echo "==> Setting up database..."
bundle exec rake db:create
bundle exec rake db:schema:load

echo "==> Creating admin cluster..."
ADMIN_FNAME="Admin" ADMIN_LNAME="User" ADMIN_EMAIL="admin@example.com" \
  SUPER_ADMIN=y bundle exec rake db:new_cluster 2>&1 | grep -v QueryTrace

# ── 9. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
echo "  Gather is ready!"
echo "  Start with:  cd $GATHER_DIR && bin/dev"
echo "  URL:         https://gatherdev.org:3000"
echo "  Login:       admin@example.com"
echo "════════════════════════════════════════════"
