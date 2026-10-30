# Updating Bambuddy

> **0.2.3 note:** the in-app **Update** button is unreliable when upgrading from
> older releases. Use the commands below instead — they cover every supported
> install path and are safe to run repeatedly.

Pick the section that matches how Bambuddy was installed.

---

## Docker

```bash
# 1. Make sure your compose file isn't pinned to an old version.
#    The image line should read one of:
#      image: ghcr.io/maziggy/bambuddy:latest
#      image: ghcr.io/maziggy/bambuddy:0.2.3
#    If it pins an older tag (e.g. :0.2.2.2), edit it first.

# 2. Pull and restart
docker compose pull
docker compose up -d
```

**If your `docker-compose.yml` is older than 0.2.3,** also refresh it from the
repo — recent releases added `cap_add: NET_BIND_SERVICE`, extra virtual-printer
ports for bridge mode, and an optional Postgres block:

```bash
curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/docker-compose.yml \
  -o docker-compose.yml.new
# Diff against yours, merge by hand, then:
docker compose up -d
```

---

## Native install (`install.sh` or manual `git clone`)

Both paths produce a git working tree at the install directory, so the update
is the same. Preferred:

```bash
sudo /opt/bambuddy/install/update.sh
```

`update.sh` stops the service, snapshots the database via the built-in backup
API, fast-forwards to `origin/main`, installs Python deps, rebuilds the
frontend, and restarts the service. It rolls back automatically if any step
fails.

### Updating from the Fork (`piyushagade/bambuddy`)

#### 1. Local Machine (Before Pushing)
Always rebuild the frontend locally before committing so `static/` contains the latest production bundle (in case the remote server does not have `npm` installed):

```powershell
cd frontend
npm run build
cd ..
git add backend/ frontend/ static/
git commit -m "Describe your changes"
git push origin main
```

#### 2. Remote Server (`abe` / `/opt/bambuddy`)
On the first switch to the fork, allow `root` (`sudo`) to run `git` inside `/opt/bambuddy` and point `origin` at the fork:

```bash
cd /opt/bambuddy

# One-time setup: allow root (sudo) to operate on /opt/bambuddy and point origin to the fork
sudo git config --global --add safe.directory /opt/bambuddy
sudo git remote set-url origin https://github.com/piyushagade/bambuddy.git

# Run the updater (backs up data, stops service, resets to origin/main, updates deps, and restarts)
sudo /opt/bambuddy/install/update.sh
```

If `bambuddy.service` ever encounters file-ownership issues after `sudo` runs `git reset --hard`, restore ownership to the `bambuddy` service user:

```bash
sudo chown -R bambuddy:bambuddy /opt/bambuddy
sudo systemctl restart bambuddy
```

#### 3. Integrating Updates from Upstream (`maziggy/bambuddy`)
The original repository is configured as the `upstream` remote (`https://github.com/maziggy/bambuddy.git`).

**Option A: Full Sync (`git merge upstream/main`)**
```powershell
# 1. Fetch upstream and inspect incoming commits
git fetch upstream
git log HEAD..upstream/main --oneline

# 2. Merge into local main
git merge upstream/main
```
When resolving merge conflicts:
- **`static/` (Compiled Frontend Assets)**: Never resolve `static/` conflicts by hand. Resolve source conflicts in `frontend/src/` and `backend/` first, then regenerate `static/` cleanly:
  ```powershell
  cd frontend
  npm install
  npm run build
  cd ..
  git add static/
  ```
- **`backend/app/core/database.py` (Startup Migrations)**: Keep both upstream's new `_migrate_*()` calls and the fork's `_migrate_*()` calls inside `init_db()`.
- **Shared UI / i18n Files (`SettingsPage.tsx`, `Layout.tsx`, `en.ts`)**: Keep both upstream sections and fork-specific sections (cohort onboarding, user expiration, filament deficit), then commit and push:
  ```powershell
  git commit -m "chore: merge upstream/main into fork"
  git push origin main
  ```

**Option B: Selective Feature Sync (`git cherry-pick`)**
To pull only a specific upstream improvement (e.g., support for a new printer model) without merging everything:
```powershell
git fetch upstream
git log upstream/main --oneline --grep="printer" -n 20
git cherry-pick <commit-hash>

cd frontend && npm run build && cd ..
git add static/
git commit --amend --no-edit
git push origin main
```

### Manual equivalent

If you'd rather run the steps yourself:

```bash
cd /opt/bambuddy
sudo systemctl stop bambuddy
sudo -u bambuddy git fetch origin
sudo -u bambuddy git reset --hard origin/main
sudo -u bambuddy venv/bin/pip install -r requirements.txt
sudo systemctl start bambuddy
```

Replace `/opt/bambuddy` with your install path if different. Database schema
migrations run automatically on startup — no Alembic step is required.

---

## Installed from a GitHub ZIP or tarball download

These installs have no `.git` directory, so neither `update.sh` nor a plain
`git pull` will work. Reinstall cleanly:

```bash
# 1. Back up your stateful data
sudo systemctl stop bambuddy
sudo tar czf ~/bambuddy-backup.tgz -C /opt/bambuddy \
  data bambuddy.db bambuddy.db-shm bambuddy.db-wal \
  virtual_printer archive projects icons .env 2>/dev/null || true

# 2. Remove the old install and reinstall via install.sh
sudo rm -rf /opt/bambuddy
curl -fsSL https://raw.githubusercontent.com/maziggy/bambuddy/main/install/install.sh \
  -o /tmp/install.sh && sudo bash /tmp/install.sh --path /opt/bambuddy

# 3. Restore your data
sudo systemctl stop bambuddy
sudo tar xzf ~/bambuddy-backup.tgz -C /opt/bambuddy
sudo systemctl start bambuddy
```

---

## Before you upgrade

Take a backup. Settings → Backup → **Create Backup** downloads a ZIP containing
the database and all stateful directories. Any bare-metal update via
`update.sh` does this automatically; Docker and manual upgrades do not.
