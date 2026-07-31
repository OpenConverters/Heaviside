#!/usr/bin/env bash
#
# deploy_heaviside_code.sh — update prod's Heaviside CODE (not its data).
#
# WHY THIS EXISTS (ABT #419). Prod drifted 121 commits / 4 weeks behind main because it
# cannot reach GitHub: `ssh -T git@github.com` from the box returns "Permission denied
# (publickey)", so `git pull` has never worked there. Nobody noticed, because the DATA
# deploy (deploy_tas_data.sh) kept succeeding and the service kept answering.
#
# It is deliberately NOT a bare rsync of the tree. A code-only copy is what broke prod on
# 2026-07-31: one file was copied onto a 121-commit-old tree, it imported 8 modules that
# did not exist there, and /crossref returned HTTP 500 until the file was reverted. The
# same jump also needs:
#   - 2 new runtime deps (openai>=1.40, openpyxl>=3.1). openai is not optional: Otto, Ray
#     and Nicola — the crossref review agents — RAISE AT CONSTRUCTION without it, so the
#     endpoint would 500 exactly as before.
#   - 11 schema submodule pins moved (PEAS/CAS/SAS/RAS/MAS/CIAS/CONAS/CTAS/AAS/TDAS/TAS).
#
# PREREQUISITE, ONE TIME: authorise prod's key as a read-only deploy key on
# github.com/OpenConverters/Heaviside → Settings → Deploy keys → Add:
#   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOdX8zDfnsvzVZFxhQJiDhvE5Kah3ZKwRJYf9CVDgRtW root@openmagnetics
# Without it step 1 fails and the script stops before touching anything.
#
#   scripts/deploy_heaviside_code.sh              # dry run: report the gap, change nothing
#   scripts/deploy_heaviside_code.sh --apply
#
set -euo pipefail

PROD_HOST="${PROD_HOST:-root@51.15.253.66}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/om_scaleway}"
PROD_DIR="${PROD_DIR:-/home/alf/OpenConverters/Heaviside}"
APPLY=""
[ "${1:-}" = "--apply" ] && APPLY=1

SSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=20"

# Everything that touches the repo, the venv or the app's own files runs as ALF, never as
# root. The tree is alf:ubuntu and supervisor starts heaviside with user=alf, so a git
# pull or pip install performed as root leaves root-owned files inside an alf-owned tree
# that the service then cannot write. Only supervisorctl runs as root.
R() { $SSH "$PROD_HOST" "su alf -c 'cd $PROD_DIR && $*'"; }
ROOT() { $SSH "$PROD_HOST" "$*"; }
say() { printf '\n=== %s ===\n' "$1"; }

say "0. Where prod is vs origin/main"
PROD_SHA=$(R 'git rev-parse HEAD')
echo "prod HEAD : $PROD_SHA  ($(R "git show -s --format=%ci $PROD_SHA"))"
echo "local HEAD: $(git rev-parse HEAD)  ($(git show -s --format=%ci HEAD))"
if git cat-file -e "$PROD_SHA" 2>/dev/null; then
  echo "prod is behind by: $(git rev-list --count "$PROD_SHA"..HEAD) commits, \
$(git diff --name-only "$PROD_SHA"..HEAD | wc -l) files"
else
  echo "prod HEAD is unknown locally — fetch first."
fi

say "1. Can prod reach GitHub?"
if ROOT 'ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -T git@github.com 2>&1 | head -1' \
     | grep -q 'successfully authenticated'; then
  echo "yes"
else
  echo "NO — prod cannot authenticate to GitHub."
  echo "Add this as a deploy key on OpenConverters/Heaviside, then re-run:"
  ROOT 'cat /root/.ssh/id_ed25519.pub' || true
  exit 1
fi

say "2. Prod's tree must be clean (never discard someone's WIP silently)"
DIRTY=$(R 'git -c submodule.recurse=false status --porcelain -uno | head -20')
if [ -n "$DIRTY" ]; then
  echo "prod has local modifications — refusing:"; echo "$DIRTY"; exit 1
fi
echo "clean"

if [ -z "$APPLY" ]; then
  say "DRY RUN"
  echo "Would: git pull --ff-only; git submodule update --init --recursive;"
  echo "       .venv/bin/pip install -e . ; supervisorctl restart heaviside; verify."
  exit 0
fi

say "3. Pull code + submodules"
R 'git pull --ff-only'
R 'git submodule update --init --recursive'

say "4. Sync runtime deps (openai/openpyxl are REQUIRED by the crossref agents)"
R '.venv/bin/pip install -q -e . 2>&1 | tail -3'

say "5. Restart"
ROOT 'supervisorctl restart heaviside' || true
sleep 12

say "6. Verify it actually RUNS — not just that files landed"
HEALTH=$($SSH "$PROD_HOST" "curl -s -o /dev/null -w '%{http_code}' -m 30 http://127.0.0.1:8774/health" || echo 000)
echo "/health -> $HEALTH"
# The import path that broke last time: crossref pulls run_crossref_pipeline at request
# time, so a missing module shows up as a 500 here and NOWHERE in /health.
IMPORTOK=$(R '.venv/bin/python -c "from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline; print(\"ok\")" 2>&1 | tail -1')
echo "crossref import -> $IMPORTOK"
if [ "$HEALTH" != "200" ] || [ "$IMPORTOK" != "ok" ]; then
  echo "DEPLOY UNHEALTHY. Roll back with:"
  echo "  $SSH $PROD_HOST 'cd $PROD_DIR && git reset --hard $PROD_SHA && \\"
  echo "     git submodule update --init --recursive && supervisorctl restart heaviside'"
  exit 1
fi
echo "prod code updated and serving."
