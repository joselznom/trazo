#!/usr/bin/env python3
"""Check the template example's content after the project's generic verify."""

from pathlib import Path
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET


def verify(path: Path) -> None:
    text = subprocess.run(["pandoc", str(path), "-t", "plain"], check=True,
                          capture_output=True, text=True).stdout
    for expected in ("References", "Pandoc", "Figure 1", "Table 1", "Internal site"):
        if expected not in text:
            raise SystemExit(f"{path}: missing example text: {expected}")
    with zipfile.ZipFile(path) as docx:
        document = ET.fromstring(docx.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = [(paragraph.find('w:pPr/w:pStyle', ns),
                   "".join(paragraph.itertext())) for paragraph in document.findall('.//w:body/w:p', ns)]
    styled = [(style.get(f"{{{ns['w']}}}val") if style is not None else "", text)
              for style, text in paragraphs]
    if not any(style == "Author" and "First Author" in text and "Second Author" in text
               for style, text in styled):
        raise SystemExit(f"{path}: missing structured author line")
    if not any(style == "Affiliation" and "Example University" in text for style, text in styled):
        raise SystemExit(f"{path}: missing Affiliation paragraph")
    abstract = next(index for index, (_, text) in enumerate(styled) if text == "Abstract")
    keywords = next(index for index, (style, _) in enumerate(styled) if style == "Keywords")
    introduction = next(index for index, (_, text) in enumerate(styled) if text == "Introduction")
    if not abstract + 1 < keywords < introduction:
        raise SystemExit(f"{path}: Keywords must follow the abstract before Introduction")
    headings = [text for _, text in styled]
    if not headings.index("Conclusion") < headings.index("Declarations") < headings.index("References"):
        raise SystemExit(f"{path}: declarations must follow Conclusion and precede References")
    if "PLACEHOLDER" in text:
        raise SystemExit(f"{path}: example contains a placeholder")
    if document.find(".//w:vMerge", ns) is None:
        raise SystemExit(f"{path}: missing vertical cell merge")
    if document.find('.//w:gridSpan[@w:val="2"]', ns) is None:
        raise SystemExit(f"{path}: missing two-column cell merge")
    print(f"Example content verified: {path}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
