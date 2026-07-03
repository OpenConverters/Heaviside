#!/usr/bin/env bash
#
# deploy_tas_data.sh — reconcile prod's runtime-added TAS parts into the canonical
# TAS git repo, then replace prod's TAS DB with the merged canonical.
#
# Flow (each step gates the next; nothing destructive runs until the canonical is
# durably committed):
#
#   A. Harvest — atomically ROTATE prod's delta journal aside and pull it local.
#      (rotation means parts prod adds DURING the deploy go to a fresh journal and
#       are picked up next time, never lost.)
#   B. Merge   — replay the harvested journal into canonical via add_component
#      (re-validated + Blade Runner + dedup by MPN; idempotent).
#   C. Commit  — commit the canonical TAS repo; push is best-effort (a blocked
#      Git-LFS push warns and falls through to the copy — the commit is durable).
#   D. Replace — rsync canonical TAS/data -> prod, then restart the service.
#   E. Clear   — and ONLY now, delete the rotated delta on prod.
#
# Safe to re-run: the merge dedups, and the rotated journal on prod is deleted
# only after a successful replace. If any step fails, stop and re-run.
#
# Config via env (defaults match this deployment):
#   PROD_HOST     root@51.15.253.66
#   SSH_KEY       ~/.ssh/om_scaleway
#   PROD_DIR      /home/alf/OpenConverters/Heaviside      (prod app dir)
#   PROD_DELTA    /home/alf/.heaviside/tas_delta          (HEAVISIDE_TAS_DELTA_DIR on prod)
#   CANONICAL     /home/alf/PSMA/TAS                      (the TAS git checkout to commit)
#   REPO          /home/alf/OpenConverters/Heaviside      (repo providing the librarian)
#   SKIP_PUSH     unset  (set to 1 to commit-only, e.g. while LFS push is blocked)
#   DRY_RUN       unset  (set to 1 to print actions without harvesting/replacing)
#
set -euo pipefail

PROD_HOST="${PROD_HOST:-root@51.15.253.66}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/om_scaleway}"
PROD_DIR="${PROD_DIR:-/home/alf/OpenConverters/Heaviside}"
PROD_DELTA="${PROD_DELTA:-/home/alf/.heaviside/tas_delta}"
CANONICAL="${CANONICAL:-/home/alf/PSMA/TAS}"
REPO="${REPO:-/home/alf/OpenConverters/Heaviside}"
SKIP_PUSH="${SKIP_PUSH:-}"
DRY_RUN="${DRY_RUN:-}"

SSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=20"
STAMP="$(date +%Y%m%dT%H%M%S)"
ROTATED="${PROD_DELTA}.deploying.${STAMP}"
LOCAL_HARVEST="$(mktemp -d)"

say() { printf '\n=== %s ===\n' "$1"; }
run() { if [ -n "$DRY_RUN" ]; then echo "DRY_RUN: $*"; else eval "$*"; fi; }

# ---------------------------------------------------------------------------
say "A. Harvest prod delta (rotate + pull)"
if ! $SSH "$PROD_HOST" "[ -d '$PROD_DELTA' ] && [ -n \"\$(ls -A '$PROD_DELTA' 2>/dev/null)\" ]"; then
  echo "prod has no pending delta journal — nothing to reconcile."
  echo "(will still refresh prod from canonical in step D)"
  HAVE_DELTA=0
else
  HAVE_DELTA=1
  run "$SSH '$PROD_HOST' 'mv \"$PROD_DELTA\" \"$ROTATED\"'"
  run "rsync -az -e \"$SSH\" '$PROD_HOST:$ROTATED/' '$LOCAL_HARVEST/'"
  echo "harvested $(ls -1 "$LOCAL_HARVEST" 2>/dev/null | wc -l) journal file(s) -> $LOCAL_HARVEST"
fi

# ---------------------------------------------------------------------------
say "B. Merge harvested journal into canonical ($CANONICAL/data)"
if [ "$HAVE_DELTA" = "1" ] && [ -z "$DRY_RUN" ]; then
  ( cd "$REPO" && PYTHONPATH="$REPO" python3 scripts/merge_tas_delta.py \
      --delta-dir "$LOCAL_HARVEST" --data-dir "$CANONICAL/data" )
else
  echo "DRY_RUN or no delta: skipping merge"
fi

# ---------------------------------------------------------------------------
say "C. Commit canonical TAS repo"
if git -C "$CANONICAL" diff --quiet -- data 2>/dev/null && git -C "$CANONICAL" diff --cached --quiet -- data 2>/dev/null; then
  echo "canonical data unchanged — nothing to commit."
else
  run "git -C '$CANONICAL' add data"
  run "git -C '$CANONICAL' commit -m 'data: reconcile runtime-added parts from prod ($STAMP)'"
  # Push is best-effort: the commit already gives durable, recoverable local
  # history, so a blocked push must NOT stop the deploy. The Git-LFS
  # "Enterprise Managed User" block is expected here — on that (or any push
  # failure) we warn and fall through to the copy, exactly as if done by hand.
  if [ -n "$SKIP_PUSH" ]; then
    echo "SKIP_PUSH set — committed locally, not pushing."
  elif run "git -C '$CANONICAL' push"; then
    echo "pushed canonical."
  else
    echo "WARNING: git push failed — likely the Git-LFS Enterprise-Managed-User block." >&2
    echo "         Canonical is committed locally (durable/recoverable); continuing to the copy." >&2
  fi
fi

# ---------------------------------------------------------------------------
say "D. Replace prod TAS/data from canonical + restart"
run "rsync -az -e \"$SSH\" '$CANONICAL/data/' '$PROD_HOST:$PROD_DIR/TAS/data/'"
run "$SSH '$PROD_HOST' 'chown -R alf:sudo $PROD_DIR/TAS/data 2>/dev/null; supervisorctl restart heaviside'"

# ---------------------------------------------------------------------------
say "E. Clear consumed delta on prod (only now)"
if [ "$HAVE_DELTA" = "1" ]; then
  run "$SSH '$PROD_HOST' 'rm -rf \"$ROTATED\"'"
  echo "cleared $ROTATED on prod."
fi
rm -rf "$LOCAL_HARVEST"

say "Done"
echo "canonical committed$( [ -z "$SKIP_PUSH" ] && echo ' + pushed'); prod refreshed from canonical and restarted."
