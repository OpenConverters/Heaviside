#!/usr/bin/env bash
#
# deploy_heaviside_code.sh — update prod's Heaviside CODE (not its data), via git bundle.
#
# Prod cannot reach GitHub (`ssh -T git@github.com` -> "Permission denied (publickey)"),
# which is how it drifted 122 commits / 4 weeks behind main without anyone noticing: the
# DATA deploy kept succeeding and the service kept answering. It does not need to reach
# GitHub. Prod already has every parent commit, so a THIN BUNDLE of just the new commits
# carries the whole update — the 122-commit jump on 2026-08-01 was 662 KB — over the same
# ssh key used for data. Prod's git history ends up genuinely correct, not a pile of
# rsynced files with stale metadata.
#
# It is NOT an rsync of the tree. A code-only copy is what broke prod on 2026-07-31: one
# file onto a 121-commit-old tree, importing 8 modules that did not exist there, /crossref
# 500 until it was reverted.
#
# THREE THINGS THIS REFUSES TO DO, each learned the hard way:
#
#   1. NEVER `git stash push -u`. On this box .venv is a SYMLINK to
#      /cache/heaviside-offload/venv and is not gitignored, so -u DELETED it, along with
#      prod's untracked TBAS checkout (27 paths). Nothing looked wrong at the time,
#      because a running process holds deleted files open — prod kept serving from a venv
#      that no longer existed, and the next restart would have taken it down. "Untracked"
#      on a server means symlinks, sibling checkouts and app state, not scratch files.
#      Only TRACKED modifications are stashed here; untracked files are reported and left
#      exactly where they are.
#   2. Never discard a local change without capturing it. The full diff is saved to a
#      patch on prod AND pulled local before anything is touched.
#   3. Never trust /health as proof. The 2026-07-31 breakage was a missing import inside
#      the /crossref request path — /health returned 200 throughout. This imports
#      run_crossref_pipeline explicitly.
#
# Runs as ALF, not root: the tree is alf:ubuntu and supervisor starts heaviside with
# user=alf, so root-owned files from a pull would be unwritable by the service. Only
# supervisorctl runs as root.
#
#   scripts/deploy_heaviside_code.sh              # dry run: report the gap, change nothing
#   scripts/deploy_heaviside_code.sh --apply
#
set -euo pipefail

PROD_HOST="${PROD_HOST:-root@51.15.253.66}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/om_scaleway}"
PROD_DIR="${PROD_DIR:-/home/alf/OpenConverters/Heaviside}"
BRANCH="${BRANCH:-main}"
APPLY=""
[ "${1:-}" = "--apply" ] && APPLY=1

SSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=20"
STAMP="$(git log -1 --format=%h)-$(git rev-parse --short HEAD)"
BUNDLE_LOCAL="/tmp/heaviside_${STAMP}.bundle"
BUNDLE_REMOTE="/tmp/heaviside_${STAMP}.bundle"

# `su alf -c` starts in alf's HOME, so every command must carry its own cd — omitting it
# silently runs in the wrong directory and git reports "not a git repository".
R() { $SSH "$PROD_HOST" "su alf -c 'cd $PROD_DIR; $*'"; }
ROOT() { $SSH "$PROD_HOST" "$*"; }
say() { printf '\n=== %s ===\n' "$1"; }

say "0. Gap between prod and local $BRANCH"
PROD_SHA=$(R 'git rev-parse HEAD')
echo "prod HEAD : $PROD_SHA"
echo "local HEAD: $(git rev-parse HEAD)"
if ! git cat-file -e "$PROD_SHA" 2>/dev/null; then
  echo "prod's commit is unknown locally — fetch/pull this repo first."; exit 1
fi
BEHIND=$(git rev-list --count "$PROD_SHA"..HEAD)
if [ "$BEHIND" = "0" ]; then echo "prod is already up to date."; exit 0; fi
echo "behind by : $BEHIND commits, $(git diff --name-only "$PROD_SHA"..HEAD | wc -l) files"

say "1. Prod's local state — captured, never assumed away"
PATCH="/tmp/prod_local_$(date +%Y%m%d%H%M%S).patch"
R "git diff --ignore-submodules=all > $PATCH; stat -c %s $PATCH" >/dev/null || true
MODIFIED=$(R 'git diff --ignore-submodules=all --name-only | wc -l')
UNTRACKED=$(R 'git status --porcelain --ignore-submodules=all -uall 2>/dev/null | grep -c "^??" || true')
echo "modified tracked files: $MODIFIED   untracked: $UNTRACKED"
echo "diff saved on prod at : $PATCH"
if [ "$MODIFIED" != "0" ]; then
  echo "modified files:"; R 'git diff --ignore-submodules=all --name-only | head -30'
fi

if [ -z "$APPLY" ]; then
  say "DRY RUN"
  echo "Would: bundle $PROD_SHA..HEAD -> scp -> git pull --ff-only from the bundle"
  echo "       stash TRACKED modifications only (never -u), restart, verify import+health."
  exit 0
fi

say "2. Build and ship the bundle"
git bundle create "$BUNDLE_LOCAL" "$PROD_SHA..$BRANCH"
echo "bundle: $(stat -c %s "$BUNDLE_LOCAL") bytes"
scp -q -i "$SSH_KEY" -o StrictHostKeyChecking=no "$BUNDLE_LOCAL" "$PROD_HOST:$BUNDLE_REMOTE"
ROOT "chown alf $BUNDLE_REMOTE"
R "git bundle verify $BUNDLE_REMOTE" | tail -2

say "3. Stash TRACKED modifications only (-u would delete .venv and sibling checkouts)"
if [ "$MODIFIED" != "0" ]; then
  # EXCLUDE .venv explicitly. Avoiding -u is not sufficient: .gitignore said
  # ".venv/", which matches a directory, and prod's .venv is a SYMLINK — so it
  # was never ignored, someone git-added it, and `git stash push` then carried
  # prod's interpreter off as a "tracked modification". That took prod down on
  # 2026-08-24 (health 000, ".venv MISSING") until the stash was popped back.
  R "git stash push -m 'prod local state before $STAMP deploy' -- . ':(exclude).venv'" | tail -1
  echo "recover with: git stash list / git stash show -p stash@{0}"
else
  echo "nothing to stash"
fi

# Whatever the index claimed, the interpreter must still be here. Checking now
# means a mistake costs one aborted deploy, not a restart into a missing venv.
if [ "$(R 'test -x .venv/bin/python && echo ok || echo MISSING')" != "ok" ]; then
  echo "ABORT: .venv/bin/python is gone after the stash — prod would not restart." >&2
  echo "Restore it with: git stash pop   (run as alf, in $PROD_DIR)" >&2
  exit 1
fi

say "4. Pull from the bundle"
R "git -c submodule.recurse=false pull --ff-only $BUNDLE_REMOTE $BRANCH" | tail -3
R 'git log --oneline -1'

# 4b. SCHEMAS. The pull deliberately does not recurse (a submodule fetch would need
# GitHub, which prod cannot reach), so a commit that MOVES a schema pointer lands the
# pointer and not the files. That is not cosmetic: on 2026-09-05 prod's PEAS working
# tree was the commit before the one that gave provenance its verification vocabulary,
# so prod rejected 44,156 TAS records that validate everywhere else, and `git status`
# in the submodule reported 2,623 lines of "local modifications" that were nothing of
# the kind — just an old pointer describing a newer tree.
#
# Prod already HAS the objects (they travel inside the superproject's bundle), so the
# checkout is local and needs no network. Reported, never forced: a submodule whose
# tree genuinely differs from what the pointer names is a person's business, not a
# deploy script's, and this stops rather than overwriting it.
say "4b. Schemas: are prod's submodule trees the ones this commit names?"
for SM in $(R "git config --file .gitmodules --get-regexp path | awk '{print \$2}'"); do
    STATUS=$(R "git submodule status --cached $SM 2>/dev/null | cut -c1")
    [ "$STATUS" = " " ] && continue
    WANT=$(R "git ls-tree HEAD $SM | awk '{print \$3}'")
    [ -z "$WANT" ] && continue
    if R "cd $SM && git cat-file -e $WANT 2>/dev/null"; then
        if R "cd $SM && git diff --quiet && git diff --cached --quiet"; then
            R "cd $SM && git checkout -q $WANT" && echo "    $SM -> $WANT"
        elif R "cd $SM && git diff --quiet $WANT --"; then
            # The FILES are already the ones the pointer names; only HEAD lags.
            # Moving it changes no content, so this is safe and it is the common
            # shape of the drift (someone copied the schemas, nobody moved the ref).
            R "cd $SM && git checkout -q $WANT" && echo "    $SM -> $WANT (files already matched)"
        else
            echo "    $SM: SKIPPED — the working tree has changes the pointer does not"
            echo "      explain. Inspect before syncing:  git -C $SM diff"
        fi
    else
        echo "    $SM: SKIPPED — prod does not have commit $WANT"
    fi
done

say "5. Restart"
ROOT 'supervisorctl restart heaviside' || true
sleep 15

say "6. Verify it RUNS — health alone is not proof"
HEALTH=$(ROOT "curl -s -o /dev/null -w '%{http_code}' -m 30 http://127.0.0.1:8774/health" || echo 000)
IMPORTOK=$(R '.venv/bin/python -c "from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline; print(\"ok\")" 2>&1 | tail -1')
VENV=$(R 'test -e .venv/bin/python && echo present || echo MISSING')
echo "/health         -> $HEALTH"
echo "crossref import -> $IMPORTOK"
echo ".venv           -> $VENV"
if [ "$HEALTH" != "200" ] || [ "$IMPORTOK" != "ok" ] || [ "$VENV" != "present" ]; then
  echo
  echo "DEPLOY UNHEALTHY. Roll back:"
  echo "  $SSH $PROD_HOST \"su alf -c 'cd $PROD_DIR; git reset --hard $PROD_SHA'\""
  echo "  $SSH $PROD_HOST 'supervisorctl restart heaviside'"
  echo "Prod's pre-deploy diff is at $PATCH, and its tracked mods are in stash@{0}."
  exit 1
fi
echo
echo "prod code updated to $(git rev-parse --short HEAD) and serving."
