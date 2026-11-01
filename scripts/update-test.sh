#!/bin/sh
# `make test-update`: create a paper, update it between two template stores
# rendered from this checkout, then run scripts/trazo verify. No historical
# release tags are needed, so this also works in a single-commit repository.
# Usage: scripts/update-test.sh REPO SCRATCH
set -eu
repo=$1
dir=$2
py() { uv run --quiet --project "$repo/packages/trazo" python "$@"; }

snapshot=$(mktemp -d /tmp/trazo-update.XXXXXX)
trap 'rm -rf "$snapshot"' EXIT HUP INT TERM
# This checkout with its uncommitted changes, committed, as v99.0.2.
python3 "$repo/scripts/template_snapshot.py" "$snapshot/template"
git -C "$snapshot/template" tag v99.0.2
py "$repo/scripts/template-variants.py" --from "$snapshot/template" v99.0.2 "$snapshot/store-v99.0.2"

rm -rf "$dir"
py - "$dir" "$snapshot/store-v99.0.2" <<'PY'
import sys
from pathlib import Path
from trazo import new, variants

new.create(Path(sys.argv[1]), kind="article", example=True, title="Paper title", store=variants.Store(sys.argv[2]))
PY
printf '\nOur own sentence.\n' >> "$dir/manuscript/main/01_introduction.md"

# A later template version that changes AGENTS.md and adds a file.
printf '\nAgent template change.\n' >> "$snapshot/template/template/AGENTS.md"
printf 'New in v99.0.3.\n' > "$snapshot/template/template/NEW_IN_V99.md"
git -C "$snapshot/template" add -A
git -C "$snapshot/template" commit --quiet -m "test: change template instructions and add a file"
git -C "$snapshot/template" tag v99.0.3

# `trazo update` (the manifest path, no git) from a store rendered at
# v99.0.2 to one rendered at v99.0.3: an untouched project gets the new
# AGENTS.md and file; an edited AGENTS.md gets AGENTS.md.new; the author's
# own files, the abbreviation list among them, stay as they are.
py "$repo/scripts/template-variants.py" --from "$snapshot/template" v99.0.3 "$snapshot/store-v99.0.3"
py - "$dir" "$snapshot/store-v99.0.3" <<'PY'
import sys
from pathlib import Path
from trazo import manifest, update, variants

folder, store = Path(sys.argv[1]), variants.Store(sys.argv[2])
report = update.update(folder, store)
assert not report.up_to_date and not report.needs_merge, report
assert manifest.read(folder)["template"] == store.template == "v99.0.3"
assert (folder / "manuscript/main/01_introduction.md").read_text().endswith("Our own sentence.\n")
PY
(cd "$dir" && TRAZO_DEV="$repo/packages/trazo" scripts/trazo verify)
echo "test-update passed: current trazo new paper -> newer template with trazo update, then verify"

py - "$snapshot" <<'PY'
import sys
from pathlib import Path
from trazo import new, update, variants

snapshot = Path(sys.argv[1])
old, newer = variants.Store(snapshot / "store-v99.0.2"), variants.Store(snapshot / "store-v99.0.3")
assert (old.template, newer.template) == ("v99.0.2", "v99.0.3"), (old.template, newer.template)


def make(name, store):
    folder = snapshot / "papers" / name / "paper"
    new.create(folder, kind="article", example=True, title="Paper title", store=store)
    return folder


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


fresh = tree(make("fresh", newer))
clean, edited = make("clean", old), make("edited", old)

report = update.update(clean, newer)
assert (report.replaced, report.added, report.needs_merge, report.removed, report.skipped) == (
    ["AGENTS.md"], ["NEW_IN_V99.md"], [], [], []), report
assert tree(clean) == fresh, sorted(k for k in fresh.keys() | tree(clean).keys()
                                    if fresh.get(k) != tree(clean).get(k))

agents = (edited / "AGENTS.md").read_bytes() + b"\nOur own rule.\n"
(edited / "AGENTS.md").write_bytes(agents)
own = [edited / "manuscript/main/01_introduction.md", edited / "manuscript/main/00c_abbreviations.md"]
for path in own:
    path.write_bytes(path.read_bytes() + b"\nAuthor-owned content, including an unfinished definition.\n")
before = [path.read_bytes() for path in own]
report = update.update(edited, newer)
assert (report.replaced, report.added, report.needs_merge) == ([], ["NEW_IN_V99.md"], ["AGENTS.md"]), report
assert (edited / "AGENTS.md").read_bytes() == agents
assert (edited / "AGENTS.md.new").read_bytes() == fresh["AGENTS.md"]
assert [path.read_bytes() for path in own] == before
print("test-update passed: trazo update v99.0.2 -> v99.0.3: replaced, added, .new, author files kept")
PY
