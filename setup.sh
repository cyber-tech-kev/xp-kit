#!/usr/bin/env bash
# xp-kit one-time setup. Run from the root of your new repo after "Use this template":
#   ./setup.sh
# Needs the GitHub CLI (https://cli.github.com), logged in with `gh auth login`.
set -euo pipefail

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first."; exit 1; }

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
OWNER=${REPO%%/*}; NAME=${REPO##*/}
ME=$(gh api user -q .login)
say "Setting up xp-kit for $REPO"

# 1. Fill in owner/repo and add you as an officer, if they're still the defaults.
python3 - "$OWNER" "$NAME" "$ME" <<'PY'
import re, sys
owner, name, me = sys.argv[1:4]
p = "xp-config.yml"; s = open(p).read()
s = re.sub(r'(?m)^(\s*owner:\s*)""', rf'\g<1>{owner}', s, count=1)
s = re.sub(r'(?m)^(\s*repo:\s*)xp\b', rf'\g<1>{name}', s, count=1)
s = re.sub(r'(?m)^officers:\s*\[\]', f'officers: [{me}]', s, count=1)
open(p, "w").write(s)
PY
say "Config: owner=$OWNER, repo=$NAME, officers include $ME"

# 2. GitHub Pages from Actions.
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null && say "Pages: set to GitHub Actions"
else
  gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null && say "Pages: enabled"
fi

# 3. Label used by the Join XP form.
gh label create xp-join --repo "$REPO" --color 5DCAA5 --description "Join XP form" --force >/dev/null
say "Label: xp-join"

# 4. XP desk issue for /award commands.
DESK=$(gh issue list --repo "$REPO" --state open --search 'in:title "XP desk"' --json number -q '.[0].number')
if [ -z "$DESK" ]; then
  URL=$(gh issue create --repo "$REPO" --title "XP desk" \
    --body "Organizers: award XP here. Example: \`/award @username attendance 2027-01-20\`. See the README for the full syntax.")
  DESK=${URL##*/}
fi
gh issue pin "$DESK" --repo "$REPO" >/dev/null 2>&1 || true
say "XP desk: issue #$DESK (pinned)"

# 5. Optional Discord announcements.
read -r -p "Discord webhook URL for level-up posts (Enter to skip): " HOOK || true
if [ -n "${HOOK:-}" ]; then
  printf '%s' "$HOOK" | gh secret set DISCORD_WEBHOOK_XP --repo "$REPO" >/dev/null
  say "Discord: webhook saved as a secret"
else
  warn "Discord skipped. Add it later with: gh secret set DISCORD_WEBHOOK_XP"
fi

# 6. Commit the config and deploy.
python3 -c "import yaml" 2>/dev/null || pip3 install --quiet pyyaml || true
python3 scripts/xp.py validate || { warn "Fix the problems above in xp-config.yml, then push."; exit 1; }
if ! git diff --quiet xp-config.yml; then
  git add xp-config.yml && git commit -qm "Configure xp-kit for $REPO" && git push -q
  say "Pushed config"
fi
gh workflow run build.yml --repo "$REPO" >/dev/null && say "Deploy started"

SITE="https://$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/$NAME"
cat <<MSG

Done. In about a minute your board is live at:
  $SITE

Next:
  - Edit xp-config.yml (or use PROMPTS.md with any LLM) to make it yours
  - Share the join link: https://github.com/$REPO/issues/new?template=join-xp.yml
  - Award XP by commenting on issue #$DESK

If the award or join bots fail with a 403 on push, allow Actions to write:
  Settings > Actions > General > Workflow permissions > Read and write
MSG
