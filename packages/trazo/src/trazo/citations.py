"""Check that the documents' citation keys exist in the bibliography.

    trazo citations [--summary]
"""

import argparse
import re
import sys

from .expand import INLINE_CODE, ValuesError, expand_files, fenced_lines
from .project import PROJECT

# Pandoc's key syntax: a Unicode letter, digit or "_" first and last,
# punctuation only inside (@müller2019ångström, @2020x).
CITE_RE = re.compile(r"(?<![\w\\])@(\w(?:[\w:.#$%&+?<>~/\-]*\w)?)")
BIB_RE = re.compile(r"^\s*@[A-Za-z]+\s*[{(]\s*([^,\s]+)\s*,", re.MULTILINE)
XREF_PREFIXES = ("fig:", "tbl:", "sec:", "eq:")
BIBLIOGRAPHY = PROJECT.path("bibliography")


def manuscript_keys():
    keys = set()
    text = "\n".join(expand_files(PROJECT.document_files(name), document=name, mark=True)
                     for name in PROJECT.documents)
    for line, literal in fenced_lines(text.splitlines()):
        if not literal:
            prose = INLINE_CODE.sub("", line)
            prose = re.sub(r"<[^<>\s]+@[^<>\s]+>|\]\(mailto:[^)]*\)", "", prose)
            keys.update(k for k in CITE_RE.findall(prose) if not k.startswith(XREF_PREFIXES))
    return keys


def bibliography_keys():
    if not BIBLIOGRAPHY.exists():
        raise FileNotFoundError(f"{PROJECT.relative('bibliography')} does not exist")
    return set(BIB_RE.findall(BIBLIOGRAPHY.read_text(encoding="utf-8")))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo citations",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    try:
        used = manuscript_keys()
        defined = bibliography_keys()
    except (OSError, ValuesError, RecursionError, ValueError) as exc:
        print(f"trazo citations: {exc}", file=sys.stderr)
        return 1
    missing = sorted(used - defined)
    unused = sorted(defined - used)

    print(f"Used citation keys: {len(used)}")
    print(f"Bibliography keys:  {len(defined)}")
    if args.summary:
        return 0 if not missing else 1

    if missing:
        print(f"\nMissing from {PROJECT.relative('bibliography')}:")
        for key in missing:
            print(f"  - {key}")
    if unused:
        print("\nUnused bibliography keys:")
        for key in unused:
            print(f"  - {key}")
    if missing:
        return 1
    print("\nCitation check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
