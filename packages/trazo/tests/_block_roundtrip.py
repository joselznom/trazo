"""Round trip of writing on the preview: edit each block's text, build the page
again and compare it with what was typed (test_preview_blocks.py).

It also runs on a real paper, on a throwaway copy, and prints the shape of
what it found, never the paper's text:

    uv run python tests/_block_roundtrip.py /path/to/paper [--doc NAME] [--rounds N]
"""

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

CHIP, OTHER_CHIP = 0xE000, 0xF000
EMPTY = {"br", "hr", "img", "input", "meta", "link", "col", "wbr", "source", "area", "base", "embed", "track"}
NESTED_BLOCKS = {"ul", "ol", "dl", "blockquote"}
# Chips the page has that the build did not wrap: the viewer locks them too.
OTHER_CHIP_CLASSES = {"citation", "pv-val", "math", "footnote-ref"}
SMART = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "---", "–": "--", "…": "..."})
PREFIXES = {"start-list": "1. ", "start-head": "# ", "start-bullet": "- ", "start-quote": "> ", "start-table": "Table: "}
INSERTS = {"insert": "Zqxnew ", "active": "p<0.05, 5*3, a_b, $5 and [x] @y ~z^ ",
           "sentence": "A brand new clause, with commas; and “quotes”. ", "typed": "don't say \"this\" -- or that... "}
# Most common first: test_preview_blocks.py holds these to a stricter rate.
COMMON = ("replace", "typo", "delete", "join", "multi", *INSERTS)
# Bold and italic: the style, and the text it goes on: a word, or text with this style (1 bold, 2 italic).
FORMATS = {"bold": ("strong", None), "italic": ("em", None), "unbold": ("strong", 1), "unitalic": ("em", 2),
           "bold-italic": ("strong", 2), "italic-bold": ("em", 1)}
KINDS = (*COMMON, "chip", "rewrite", "split", *PREFIXES, *FORMATS)
BREAK = "\uf8ff"


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: "list[Node | str]" = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def closest(self, *tags: str) -> "Node | None":
        node = self.parent
        while node is not None and node.tag not in tags:
            node = node.parent
        return node


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = self.at = Node("root", {})

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, self.at)
        self.at.children.append(node)
        if tag not in EMPTY:
            self.at = node

    def handle_endtag(self, tag):
        node = self.at
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data):
        self.at.children.append(data)


def parse(html: str) -> Node:
    tree = _Tree()
    tree.feed(html)
    return tree.root


def _other_chip(node: Node) -> bool:
    return (node.tag in ("code", "img", "math", "svg") or bool(OTHER_CHIP_CLASSES & node.classes)
            or (node.tag == "a" and node.attrs.get("href", "").startswith("#")))


def styled_text(node: Node, count: list[int] | None = None, strong: bool = False, em: bool = False) -> list[tuple[str, bool, bool]]:
    """A block's text as the viewer sends it, each chip one private-use
    character, and with each character its bold and italic."""
    count = [0] if count is None else count
    out: list[tuple[str, bool, bool]] = []
    for child in node.children:
        if isinstance(child, str):
            out += [(ch, strong, em) for ch in child]
        elif "pv-src" in child.classes or child.tag in NESTED_BLOCKS or "pv-edit-del" in child.classes:
            continue
        elif child.tag == "br":
            out.append((" ", strong, em))
        elif "pv-atom" in child.classes:
            out.append((chr(CHIP + int(child.attrs["data-i"])), strong, em))
        elif _other_chip(child):
            out.append((chr(OTHER_CHIP + count[0]), strong, em))
            count[0] += 1
        else:
            out += styled_text(child, count, strong or child.tag == "strong", em or child.tag == "em")
    return out


def flat(node: Node) -> str:
    return "".join(ch for ch, _, _ in styled_text(node))


def squash(text: str) -> str:
    return " ".join(text.split())


def comparable(text: str) -> str:
    return squash(re.sub("[\ue000-\uf8fe]", "\ufffc", text).translate(SMART))


def page_blocks(html: str) -> dict[tuple[str, str], Node]:
    """(file, key) -> the paragraph or list item a block is on the page."""
    found = {}
    for node in parse(html).walk():
        if "pv-src" in node.classes:
            host = node.closest("p", "li")
            if host is not None and host.closest("nav", "table", "figure") is None:
                found[node.attrs["data-file"], node.attrs["data-h"]] = host
    return found


def squeezed(chars: list[tuple[str, bool, bool]]) -> list[tuple[str, bool, bool]]:
    """styled_text with the spaces of squash."""
    out: list[tuple[str, bool, bool]] = []
    for char in chars:
        if not char[0].isspace():
            out.append(char)
        elif out and out[-1][0] != " ":
            out.append((" ", char[1], char[2]))
    return out[:-1] if out and out[-1][0] == " " else out


def shown(chars: list[tuple[str, bool, bool]]) -> list[tuple[str, bool, bool]]:
    """The characters whose style can be compared: no spaces, chips or smart typography."""
    return [(plain, strong, em) for ch, strong, em in chars if not ch.isspace() and ord(ch) < CHIP
            for plain in ch.translate(SMART)]


def format_span(target: int | None, chars: list[tuple[str, bool, bool]], rng: random.Random) -> tuple[int, int] | None:
    """What a format edit selects: a word, or a run of text with style `target`."""
    if target is None:
        found = list(re.finditer(r"[^\W\d_]{3,}", "".join(ch for ch, _, _ in chars)))
    else:
        found = list(re.finditer("x+", "".join("x" if char[target] else "." for char in chars)))
    return rng.choice(found).span() if found else None


def toggled(chars: list[tuple[str, bool, bool]], span: tuple[int, int], slot: int) -> list[tuple[str, bool, bool]]:
    """The characters with a style toggled on `span`: text that all has it loses it."""
    selected = shown(chars[span[0]:span[1]])
    on = not (selected and all(char[slot] for char in selected))
    return shown([(ch, *((on if i + 1 == slot and span[0] <= n < span[1] else flag) for i, flag in enumerate(flags)))
                  for n, (ch, *flags) in enumerate(chars)])


def edited(kind: str, text: str, rng: random.Random) -> str | None:
    """`text` as the author would leave it after an edit of this kind."""
    words = list(re.finditer(r"[^\W\d_]{3,}", text))
    if kind == "chip":
        chips = [i for i, ch in enumerate(text) if CHIP <= ord(ch) < OTHER_CHIP]
        if not chips:
            return None
        at = rng.choice(chips)
        return squash(text[:at] + text[at + 1:])
    if kind == "rewrite":
        return "Fully rewritten paragraph " + " and ".join(ch for ch in text if ord(ch) >= CHIP and ord(ch) < 0xF8FF) + " with new words."
    if kind in PREFIXES:
        return PREFIXES[kind] + text
    if kind == "split":
        cuts = [m.end() for m in re.finditer(r"[.;:] (?=\S)", text)] or [m.start() for m in words[1:]]
        if not cuts:
            return None
        at = rng.choice(cuts)
        return text[:at].rstrip() + BREAK + text[at:]
    if not words:
        return None
    word = rng.choice(words)
    a, b = word.span()
    if kind == "replace":
        return text[:a] + "Zqxword" + text[b:]
    if kind == "typo":
        return text[:a + 1] + "q" + text[a + 1:]
    if kind in INSERTS:
        return text[:a] + INSERTS[kind] + text[a:]
    if kind == "delete":
        return text[:a] + text[b + 1 if b < len(text) and text[b] == " " else b:]
    if kind == "join":
        return text[:b] + text[b + 1:] if text[b:b + 1] == " " else None
    if len(words) < 2:
        return None
    first, second = sorted(rng.sample(words, 2), key=lambda m: m.start())
    return text[:first.start()] + "Zqxone" + text[first.end():second.start()] + "Zqxtwo" + text[second.end():]


def run(doc: str, rounds: int, out_dir: Path, kinds: tuple[str, ...] = KINDS) -> tuple[Counter, list[tuple[str, str, str]]]:
    """(count by (kind, right|refused|WRONG), the wrong ones as (kind, typed, on the page))."""
    from trazo import preview, preview_block_format, preview_block_save, preview_edit_review, preview_render
    from trazo.local_server import Conflict

    def build(where: Path) -> str:
        error, _ = preview_render.render(doc, where)
        if error:
            raise RuntimeError(error)
        return (where / f"{doc}.html").read_text(encoding="utf-8")

    names = preview_edit_review.page_files(doc)
    originals = {name: preview_edit_review.exact_text(preview.ROOT / name) for name in names}
    results: Counter = Counter()
    wrong: list[tuple[str, str, str]] = []
    try:
        for number in range(rounds):
            rng = random.Random(1000 + number)
            blocks = page_blocks(build(out_dir / "before"))
            saved = []
            for index, ((file, key), node) in enumerate(sorted(blocks.items(), key=lambda item: item[0])):
                chars = squeezed(styled_text(node))
                before = "".join(ch for ch, _, _ in chars)
                kind = kinds[(number + index) % len(kinds)]
                request = {"file": file, "h": key, "before": before}
                if kind in FORMATS:
                    style, target = FORMATS[kind]
                    span = format_span(target, chars, rng)
                    if span is None:
                        continue
                    wanted = [toggled(chars, span, 1 if style == "strong" else 2)]
                    send = preview_block_format.change_format
                    request |= {"style": style, "from": span[0], "to": span[1]}
                else:
                    after = edited(kind, before, rng) if before else None
                    if after is None:
                        continue
                    wanted = after.split(BREAK)
                    send = preview_block_save.change_block
                    request["after"] = after
                try:
                    answer = send(request)
                except Conflict:  # a key of equal blocks: the page has not caught up with this round's saves
                    results["why", "the file changed"] += 1
                    continue
                if answer.get("saved"):
                    saved.extend((file, part, kind, want) for part, want in zip(answer["parts"], wanted))
                else:
                    results[kind, "refused"] += 1
                    results["why", answer["refused"]] += 1
            page = page_blocks(build(out_dir / "after"))
            on_page = {comparable(flat(node)) for node in page.values()}
            for file, key, kind, after in saved:
                node = page.get((file, key))  # blocks that ended up equal have other keys
                got = squash(flat(node)) if node is not None else "(not on the page)"
                if isinstance(after, list):
                    good = node is not None and shown_styles(node) == after
                    after = "".join(ch for ch, _, _ in after)
                else:
                    good = comparable(got) == comparable(after) or (node is None and comparable(after) in on_page)
                results[kind, "right" if good else "WRONG"] += 1
                if not good:
                    wrong.append((kind, after, got))
            for name, text in originals.items():
                (preview.ROOT / name).write_bytes(text.encode("utf-8"))
    finally:
        for name, text in originals.items():
            (preview.ROOT / name).write_bytes(text.encode("utf-8"))
    return results, wrong


def shown_styles(node: Node) -> list[tuple[str, bool, bool]]:
    return shown(styled_text(node))


def _hide(text: str) -> str:
    return re.sub(r"\d", "0", re.sub(r"[^\W\d_]", "x", text))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paper", type=Path)
    parser.add_argument("--doc", default="manuscript")
    parser.add_argument("--rounds", type=int, default=len(KINDS))
    parser.add_argument("--format", action="store_true", help="only bold and italic")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="trazo-round-trip-") as folder:
        root = Path(folder) / "paper"
        shutil.copytree(args.paper, root, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".build", ".venv"))
        os.environ["TRAZO_ROOT"] = str(root)
        os.environ.pop("PREVIEW_REVIEW_DIR", None)
        os.chdir(root)
        from trazo import expand
        load = expand.load_entries
        expand.load_entries = lambda path=expand.VALUES_PATH, check_inputs=True: load(path, False)  # a copy has new dates
        results, wrong = run(args.doc, args.rounds, Path(folder) / "pages", tuple(FORMATS) if args.format else KINDS)
    print(f"{'edit':14} {'right':>6} {'refused':>8} {'WRONG':>6}")
    for kind in KINDS:
        print(f"{kind:14} {results[kind, 'right']:6} {results[kind, 'refused']:8} {results[kind, 'WRONG']:6}")
    print("refused because:", {key[1]: count for key, count in results.items() if key[0] == "why"})
    for kind, typed, got in wrong[:20]:
        print("WRONG", kind, _hide(typed)[:160], "|", _hide(got)[:160])
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    main()
