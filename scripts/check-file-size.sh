#!/usr/bin/env bash
# Keeps files small enough for an agent to read and edit whole.
#
#   scripts/check-file-size.sh               checks staged files (pre-commit hook)
#   scripts/check-file-size.sh --base <ref>  checks HEAD against its merge base with <ref> (CI)
#
# Tokens are estimated as bytes/4. The limit only ratchets down: a new file may not exceed
# it, and a file already over it may change but not grow. That way existing large files
# never block a small fix, and nobody has to split them before adopting the check.
#
# Skipped: deletions, binaries, lockfiles, notebooks and other files nobody writes by hand,
# plus anything .gitattributes marks linguist-generated, linguist-vendored or -size-limit.
#
# .githooks/pre-commit runs it on every commit once `git config core.hooksPath .githooks` is
# set (agent-playbook's `install.sh project` does that). Run it in CI too: --no-verify skips hooks.
set -euo pipefail

LIMIT_TOKENS=5000

base=""
case "${1:-}" in
  "") ;;
  --base) [ -n "${2:-}" ] || { echo "--base needs a ref" >&2; exit 2; }; base="$2" ;;
  *) sed -n '2,6p' "$0" >&2; exit 2 ;;
esac

if [ -n "$base" ]; then
  old_rev="$(git merge-base "$base" HEAD)"
  new_rev="HEAD"
  diff_args=("$old_rev" HEAD)
else
  old_rev="$(git rev-parse --verify -q HEAD || true)"
  new_rev=""  # the index: git cat-file -s :<path>
  diff_args=(--cached)
fi

limit_bytes=$((LIMIT_TOKENS * 4))

# git diff reports paths from the root, and git check-attr resolves them from the cwd.
cd "$(git rev-parse --show-toplevel)"

skipped_by_name() {
  case "$1" in
    *.lock|*-lock.json|*-lock.yaml|*.lockb|go.sum|*.ipynb|*.min.js|*.min.css|*.map|*.svg|*.snap|*/__snapshots__/*) return 0 ;;
  esac
  return 1
}

skipped_by_attr() {
  local attrs
  attrs="$(git check-attr linguist-generated linguist-vendored size-limit -- "$1")"
  printf '%s\n' "$attrs" | grep -Eq ': linguist-(generated|vendored): (set|true)$|: size-limit: unset$'
}

size_at() {  # size_at <rev or empty for index> <path>; 0 when the path does not exist there
  git cat-file -s "$1:$2" 2>/dev/null || echo 0
}

new_files="" grown_files=""

# --numstat -z prints "added<TAB>deleted<TAB>path" per file, or an empty path followed by the
# old and new paths for a rename. Binaries show "-" counts.
while IFS= read -r -d '' record; do
  added="${record%%$'\t'*}" rest="${record#*$'\t'}"
  path="${rest#*$'\t'}" old_path=""
  if [ -z "$path" ]; then
    IFS= read -r -d '' old_path; IFS= read -r -d '' path
  fi
  [ "$added" = - ] && continue
  skipped_by_name "$path" && continue
  skipped_by_attr "$path" && continue

  new_size="$(size_at "$new_rev" "$path")"
  [ "$new_size" -le "$limit_bytes" ] && continue
  old_size=0
  [ -n "$old_rev" ] && old_size="$(size_at "$old_rev" "${old_path:-$path}")"
  [ "$new_size" -le "$old_size" ] && continue

  line="  $path: ~$((new_size / 4)) tokens"
  if [ "$old_size" -gt "$limit_bytes" ]; then
    grown_files+="$line (was ~$((old_size / 4)))"$'\n'
  else
    new_files+="$line"$'\n'
  fi
done < <(git diff "${diff_args[@]}" --numstat -z -M --diff-filter=d)

[ -z "$new_files$grown_files" ] && exit 0

{
  echo "File size check failed: the limit is ~$LIMIT_TOKENS tokens (bytes/4) per file."
  if [ -n "$new_files" ]; then
    echo
    echo "These files went over the limit. Split each one before committing:"
    printf '%s' "$new_files"
  fi
  if [ -n "$grown_files" ]; then
    echo
    echo "These files were already over the limit and grew. They may change but not grow."
    echo "Put the new code in a new module and import it; do not append to the large file:"
    printf '%s' "$grown_files"
  fi
  cat <<'EOF'

How to split:
- Split by responsibility. Each new file owns one concept and is named after it; no
  utils, helpers, misc or part2 files.
- Keep public entry points where callers import them today; move the internals out and
  import them back, so no caller has to change.
- Move code verbatim. Behaviour changes go in a separate commit.
- Tests: split by the unit or behaviour under test, mirroring the source layout.
- Docs: split by topic and link the parts from a short index.
- Do not bypass the check: no --no-verify, no squeezing code onto fewer lines, no marking
  the file as generated. If a file really has to stay this large, ask the user.
EOF
} >&2
exit 1
