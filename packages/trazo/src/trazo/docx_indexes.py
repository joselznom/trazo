"""Word index fields and abbreviation table styles for builds and exports."""

import io
import xml.etree.ElementTree as ET
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
INDEXES = {
    "toc": ("Table of Contents", 'TOC \\o "1-3" \\h \\z \\u'),
    "lof": ("List of Figures", 'TOC \\h \\z \\t "Image Caption,1"'),
    "lot": ("List of Tables", 'TOC \\h \\z \\t "Table Caption,1"'),
}
# Pandoc's title block (metadata paragraphs) stays above the indexes.
TITLE_STYLES = {"Title", "Subtitle", "Author", "Affiliation", "Correspondence", "Date", "AbstractTitle", "Abstract"}
# An index is added only when it would list something useful: contents need
# two headings, the lists one caption of their kind.
ENTRY_STYLES = {"toc": ({"Heading1", "Heading2", "Heading3"}, 2),
                "lof": ({"ImageCaption"}, 1), "lot": ({"TableCaption"}, 1)}


def element(name, **attrs):
    return ET.Element(f"{{{W}}}{name}", {f"{{{W}}}{k}": v for k, v in attrs.items()})


def xml_bytes(root, original):
    # Word uses namespace prefixes in attribute values (mc:Ignorable, etc.).
    # Retain their declarations even when ElementTree sees no tag using them.
    namespaces = dict(value for _, value in ET.iterparse(io.BytesIO(original), events=("start-ns",)))
    used = {name[1:].split("}")[0] for node in root.iter()
            for name in (node.tag, *node.attrib) if name.startswith("{")}
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)
        if uri not in used:
            root.set("xmlns" + (":" + prefix if prefix else ""), uri)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def apply(data, indexes=()):
    """Finish marked abbreviation tables and replace trazo's index fields.

    Word fills the fields and page numbers when fields are updated. A tagged
    content control lets a cached full build remove or change its last indexes.
    The block follows Pandoc's title block and ends with a page break; an index
    with nothing to list (see ENTRY_STYLES) is left out.
    """
    if set(indexes) - INDEXES.keys():
        raise ValueError("unknown DOCX index")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        original = archive.read("word/document.xml")
        has_abbreviations = b"TrazoAbbreviationsMarker" in original
        if not indexes and b"trazo-indexes" not in original and not has_abbreviations:
            return data
        root = ET.fromstring(original)
        body = root.find("w:body", NS)
        for parent in root.iter():
            pending = False
            for node in list(parent):
                style = node.find("w:pPr/w:pStyle", NS)
                if style is not None and style.get(f"{{{W}}}val") == "TrazoAbbreviationsMarker":
                    parent.remove(node)
                    pending = True
                elif pending and node.tag == f"{{{W}}}tbl":
                    properties = node.find("w:tblPr", NS)
                    style = properties.find("w:tblStyle", NS)
                    if style is None:
                        style = ET.SubElement(properties, f"{{{W}}}tblStyle")
                    style.set(f"{{{W}}}val", "Abbreviations")
                    borders = ET.SubElement(properties, f"{{{W}}}tblBorders")
                    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                        borders.append(element(side, val="nil"))
                    pending = False
        for node in list(body):
            if node.find('w:sdtPr/w:tag[@w:val="trazo-indexes"]', NS) is not None:
                body.remove(node)
        styles = [style.get(f"{{{W}}}val") for style in body.iterfind(".//w:pPr/w:pStyle", NS)]
        indexes = [key for key in indexes
                   if sum(style in ENTRY_STYLES[key][0] for style in styles) >= ENTRY_STYLES[key][1]]
        if not indexes and b"trazo-indexes" not in original and not has_abbreviations:
            return data
        if indexes:
            control = element("sdt")
            ET.SubElement(control, f"{{{W}}}sdtPr").append(element("tag", val="trazo-indexes"))
            content = ET.SubElement(control, f"{{{W}}}sdtContent")
            for key, (title, instruction) in INDEXES.items():
                if key not in indexes:
                    continue
                heading = ET.SubElement(content, f"{{{W}}}p")
                ET.SubElement(heading, f"{{{W}}}pPr").append(element("pStyle", val="TOCHeading"))
                ET.SubElement(ET.SubElement(heading, f"{{{W}}}r"), f"{{{W}}}t").text = title
                paragraph = ET.SubElement(content, f"{{{W}}}p")
                ET.SubElement(paragraph, f"{{{W}}}r").append(element("fldChar", fldCharType="begin", dirty="true"))
                field = ET.SubElement(ET.SubElement(paragraph, f"{{{W}}}r"), f"{{{W}}}instrText")
                field.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                field.text = instruction
                ET.SubElement(paragraph, f"{{{W}}}r").append(element("fldChar", fldCharType="separate"))
                ET.SubElement(ET.SubElement(paragraph, f"{{{W}}}r"), f"{{{W}}}t").text = "Update fields in Word to display this index."
                ET.SubElement(paragraph, f"{{{W}}}r").append(element("fldChar", fldCharType="end"))
            # The document continues on the next page.
            ET.SubElement(ET.SubElement(content, f"{{{W}}}p"), f"{{{W}}}r").append(element("br", type="page"))
            start = 0
            for node in body:
                style = node.find("w:pPr/w:pStyle", NS)
                if node.tag != f"{{{W}}}p" or style is None or style.get(f"{{{W}}}val") not in TITLE_STYLES:
                    break
                start += 1
            body.insert(start, control)
        replacements = {"word/document.xml": xml_bytes(root, original)}
        if indexes:
            original = archive.read("word/settings.xml")
            settings = ET.fromstring(original)
            update = settings.find("w:updateFields", NS)
            if update is None:
                update = ET.SubElement(settings, f"{{{W}}}updateFields")
            update.set(f"{{{W}}}val", "true")
            replacements["word/settings.xml"] = xml_bytes(settings, original)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as result:
            for entry in archive.infolist():
                result.writestr(entry, replacements.get(entry.filename, archive.read(entry.filename)))
        return output.getvalue()
