#!/usr/bin/env python3
"""Write the template's Word reference document from the styles below.

Pandoc takes styles, page setup, header and footer from the reference
document, so this script owns word/styles.xml, word/document.xml (sample
content and page setup), the footer with the page number and the theme
fonts. The other parts (numbering, footnotes, settings...) are kept from
the existing file. Output is byte-stable, so `--check` can compare it.

Usage: build-reference-docx.py PATH [--check]
"""

from pathlib import Path
import io
import re
import sys
import zipfile

FONT = "Times New Roman"
MONO = "Consolas"
ACCENT = "1F3864"      # navy: headings, title, rules
GREY = "404040"        # level-3 headings
BODY = 24              # half-points: 12 pt
SMALL = 20             # 10 pt: tables, captions
NOTE = 18              # 9 pt: table notes
LINE = 276             # 1.15 line spacing (240 = single)
AFTER = 120            # 6 pt after body paragraphs
# A4 with 2.5 cm margins, in twentieths of a point.
PAGE_W, PAGE_H, MARGIN = 11906, 16838, 1418


def rule(eighths):
    """Border attributes: a navy line `eighths` eighths of a point thick."""
    return f'w:val="single" w:sz="{eighths}" w:space="1" w:color="{ACCENT}"'

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def run_props(size=None, bold=False, italic=False, color=None, font=None,
              extra=""):
    parts = []
    if font:
        parts.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" '
                     f'w:eastAsia="{font}" w:cs="{font}"/>')
    if bold:
        parts.append("<w:b/><w:bCs/>")
    if italic:
        parts.append("<w:i/><w:iCs/>")
    if color:
        parts.append(f'<w:color w:val="{color}"/>')
    parts.append(extra)
    if size:
        parts.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return "".join(parts)


def spacing(before=0, after=0, line=None):
    rule = f' w:line="{line}" w:lineRule="auto"' if line else ""
    return f'<w:spacing w:before="{before}" w:after="{after}"{rule}/>'


def style(kind, sid, name, based=None, ppr="", rpr="", extra="",
          link=None, primary=False, next_style=None):
    head = f'<w:style w:type="{kind}" w:styleId="{sid}"'
    head += ' w:default="1">' if sid in ("Normal", "DefaultParagraphFont") \
        else ">"
    body = f'<w:name w:val="{name}"/>'
    if based:
        body += f'<w:basedOn w:val="{based}"/>'
    if next_style:
        body += f'<w:next w:val="{next_style}"/>'
    if link:
        body += f'<w:link w:val="{link}"/>'
    if primary:
        body += "<w:qFormat/>"
    body += extra
    if ppr:
        body += f"<w:pPr>{ppr}</w:pPr>"
    if rpr:
        body += f"<w:rPr>{rpr}</w:rPr>"
    return head + body + "</w:style>"


HEADINGS = {
    # level: (size, bold, italic, color, space before, bottom rule)
    1: (28, True, False, ACCENT, 360, True),
    2: (BODY, True, False, ACCENT, 240, False),
    3: (BODY, True, True, GREY, 200, False),
}


def heading_styles():
    out = []
    for level in range(1, 10):
        size, bold, italic, color, before, ruled = HEADINGS.get(
            level, (BODY, False, True, GREY, 160, False))
        ppr = ("<w:keepNext/><w:keepLines/>"
               + (f"<w:pBdr><w:bottom {rule(6)}/></w:pBdr>"
                  if ruled else "")
               + spacing(before, 120 if level == 1 else 80)
               + f'<w:outlineLvl w:val="{level - 1}"/>')
        rpr = run_props(size, bold, italic, color)
        out.append(style("paragraph", f"Heading{level}", f"heading {level}",
                         "Normal", ppr, rpr, link=f"Heading{level}Char",
                         primary=True, next_style="BodyText"))
        out.append(style("character", f"Heading{level}Char",
                         f"Heading {level} Char", "DefaultParagraphFont",
                         rpr=rpr, link=f"Heading{level}"))
    return out


def table_style():
    outer = f"<w:top {rule(12)}/><w:bottom {rule(12)}/>"
    return (
        '<w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/>'
        '<w:basedOn w:val="TableNormal"/><w:uiPriority w:val="59"/>'
        f"<w:pPr>{spacing(0, 0, 240)}</w:pPr>"
        f"<w:rPr>{run_props(SMALL)}</w:rPr>"
        '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
        f"<w:tblBorders>{outer}</w:tblBorders>"
        '<w:tblCellMar><w:top w:w="29" w:type="dxa"/>'
        '<w:left w:w="85" w:type="dxa"/><w:bottom w:w="29" w:type="dxa"/>'
        '<w:right w:w="85" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblStylePr w:type="firstRow">'
        f"<w:rPr>{run_props(bold=True)}</w:rPr>"
        f"<w:tcPr><w:tcBorders><w:bottom {rule(4)}/>"
        "</w:tcBorders></w:tcPr></w:tblStylePr></w:style>")


def styles_xml():
    caption = spacing(120, 240, 240)
    styles = [
        style("paragraph", "Normal", "Normal", primary=True),
        style("paragraph", "BodyText", "Body Text", "Normal",
              spacing(0, AFTER) + '<w:jc w:val="both"/>',
              link="BodyTextChar", primary=True),
        style("paragraph", "FirstParagraph", "First Paragraph", "BodyText",
              primary=True),
        style("paragraph", "Compact", "Compact", "BodyText",
              spacing(36, 36) + '<w:jc w:val="left"/>', primary=True),
        style("paragraph", "Title", "Title", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(0, 240, 240)
              + '<w:jc w:val="center"/>', run_props(36, True, color=ACCENT),
              link="TitleChar", primary=True),
        style("character", "TitleChar", "Title Char", "DefaultParagraphFont",
              rpr=run_props(36, True, color=ACCENT), link="Title"),
        style("paragraph", "Subtitle", "Subtitle", "Title",
              spacing(0, 240, 240), run_props(28, False, True),
              link="SubtitleChar", primary=True),
        style("character", "SubtitleChar", "Subtitle Char",
              "DefaultParagraphFont", rpr=run_props(28, False, True,
                                                     ACCENT),
              link="Subtitle"),
        style("paragraph", "Author", "Author", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(0, 60)
              + '<w:jc w:val="center"/>', primary=True),
        style("paragraph", "Affiliation", "Affiliation", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(0, 60)
              + '<w:jc w:val="center"/>', run_props(SMALL), primary=True),
        style("paragraph", "Correspondence", "Correspondence", "Affiliation",
              '<w:keepNext/><w:keepLines/>' + spacing(0, 120),
              run_props(SMALL), primary=True),
        style("paragraph", "Keywords", "Keywords", "BodyText",
              spacing(120, AFTER), run_props(BODY), primary=True),
        style("paragraph", "Date", "Date", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(120, 240)
              + '<w:jc w:val="center"/>', run_props(SMALL + 2),
              primary=True),
        style("paragraph", "AbstractTitle", "Abstract Title", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(360, 120)
              + '<w:jc w:val="center"/>', run_props(BODY, True, color=ACCENT),
              primary=True),
        style("paragraph", "Abstract", "Abstract", "Normal",
              spacing(0, AFTER) + '<w:ind w:left="567" w:right="567"/>'
              + '<w:jc w:val="both"/>', run_props(22), primary=True),
        style("paragraph", "Bibliography", "Bibliography", "Normal",
              spacing(0, 80) + '<w:ind w:left="567" w:hanging="567"/>',
              run_props(22), primary=True),
        *heading_styles(),
        style("paragraph", "BlockText", "Block Text", "BodyText",
              spacing(120, 120) + '<w:ind w:left="567" w:right="567"/>',
              run_props(22), primary=True),
        style("paragraph", "FootnoteText", "footnote text", "Normal",
              spacing(0, 60, 240) + '<w:jc w:val="both"/>', run_props(SMALL),
              primary=True),
        style("paragraph", "FootnoteBlockText", "Footnote Block Text",
              "FootnoteText", spacing(60, 60)
              + '<w:ind w:left="480" w:right="480"/>', primary=True),
        style("character", "DefaultParagraphFont", "Default Paragraph Font",
              extra='<w:uiPriority w:val="1"/><w:semiHidden/>'
                    '<w:unhideWhenUsed/>'),
        table_style(),
        style("table", "Abbreviations", "Abbreviations", "TableNormal",
              ppr=spacing(0, 60, 240), rpr=run_props(SMALL),
              extra='<w:tblPr><w:tblBorders>' + ''.join(
                  f'<w:{side} w:val="nil"/>' for side in
                  ("top", "left", "bottom", "right", "insideH", "insideV"))
                  + '</w:tblBorders></w:tblPr>'),
        style("paragraph", "DefinitionTerm", "Definition Term", "Normal",
              '<w:keepNext/><w:keepLines/>' + spacing(0, 0), run_props(
                  bold=True), primary=True),
        style("paragraph", "Definition", "Definition", "Normal",
              spacing(0, AFTER), primary=True),
        style("paragraph", "Caption", "caption", "Normal",
              caption + '<w:jc w:val="both"/>', run_props(SMALL),
              primary=True),
        style("paragraph", "TableCaption", "Table Caption", "Caption",
              "<w:keepNext/>" + spacing(240, 120, 240), primary=True),
        style("paragraph", "ImageCaption", "Image Caption", "Caption",
              primary=True),
        # Table notes (abbreviations, symbols, sources) under the table.
        style("paragraph", "TableNote", "Table Note", "Normal",
              spacing(60, 60, 240) + '<w:jc w:val="both"/>', run_props(NOTE),
              primary=True),
        style("paragraph", "Figure", "Figure", "Normal",
              spacing(240, 0, 240) + '<w:jc w:val="center"/>', primary=True),
        style("paragraph", "CaptionedFigure", "Captioned Figure", "Figure",
              "<w:keepNext/>", primary=True),
        style("character", "BodyTextChar", "Body Text Char",
              "DefaultParagraphFont", link="BodyText"),
        style("character", "VerbatimChar", "Verbatim Char", "BodyTextChar",
              rpr=run_props(SMALL, font=MONO)),
        style("character", "SectionNumber", "Section Number",
              "BodyTextChar"),
        style("character", "FootnoteReference", "footnote reference",
              "BodyTextChar", rpr='<w:vertAlign w:val="superscript"/>'),
        style("character", "Hyperlink", "Hyperlink", "BodyTextChar",
              rpr=f'<w:color w:val="{ACCENT}"/>'),
        style("paragraph", "TOCHeading", "TOC Heading", "Heading1",
              '<w:outlineLvl w:val="9"/>', primary=True),
        style("paragraph", "Footer", "footer", "Normal",
              spacing(0, 0, 240) + '<w:jc w:val="right"/>',
              run_props(SMALL), link="FooterChar"),
        style("character", "FooterChar", "Footer Char",
              "DefaultParagraphFont", rpr=run_props(SMALL), link="Footer"),
    ]
    defaults = (
        f"<w:docDefaults><w:rPrDefault><w:rPr>{run_props(BODY, font=FONT)}"
        '<w:lang w:val="en-US" w:eastAsia="en-US" w:bidi="ar-SA"/>'
        "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
        f"{spacing(0, 0, LINE)}</w:pPr></w:pPrDefault></w:docDefaults>")
    latent = ('<w:latentStyles w:defLockedState="0" w:defUIPriority="99" '
              'w:defSemiHidden="0" w:defUnhideWhenUsed="0" '
              'w:defQFormat="0" w:count="276"/>')
    table_normal = (
        '<w:style w:type="table" w:default="1" w:styleId="TableNormal">'
        '<w:name w:val="Normal Table"/><w:uiPriority w:val="99"/>'
        '<w:semiHidden/><w:unhideWhenUsed/><w:tblPr>'
        '<w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar>'
        '<w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
        '</w:tblCellMar></w:tblPr></w:style>')
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        f'<w:styles xmlns:w="{W}">{defaults}{latent}'
        + "".join(styles) + table_normal + "</w:styles>")


def para(sid, text, extra=""):
    return (f'<w:p><w:pPr><w:pStyle w:val="{sid}"/></w:pPr>{extra}'
            f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')


def sample_table():
    def cell(text, header=False, align="left"):
        run = "<w:rPr><w:b/></w:rPr>" if header else ""
        return (f'<w:tc><w:p><w:pPr><w:pStyle w:val="Compact"/>'
                f'<w:jc w:val="{align}"/></w:pPr><w:r>{run}'
                f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:tc>')

    rows = [("Group", "n", "Score"), ("Control", "8", "12.4"),
            ("Treated", "8", "15.1")]
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="Table"/>'
           '<w:tblW w:type="pct" w:w="5000"/>'
           '<w:tblLook w:firstRow="1" w:lastRow="0" w:firstColumn="0" '
           'w:lastColumn="0" w:noHBand="0" w:noVBand="0" w:val="0020"/>'
           '</w:tblPr><w:tblGrid><w:gridCol w:w="3000"/>'
           '<w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>']
    for i, row in enumerate(rows):
        header = '<w:trPr><w:tblHeader w:val="on"/></w:trPr>' if i == 0 \
            else ""
        out.append("<w:tr>" + header + cell(row[0])
                   + cell(row[1], align="right")
                   + cell(row[2], align="right") + "</w:tr>")
    return "".join(out) + "</w:tbl>"


def document_xml():
    body = "".join([
        para("Title", "Paper title"),
        para("Subtitle", "Subtitle"),
        para("Author", "First Author"),
        para("Date", "Date"),
        para("AbstractTitle", "Abstract"),
        para("Abstract", "Abstract text, justified and indented."),
        *(para(f"Heading{n}", f"Heading {n}") for n in range(1, 10)),
        para("FirstParagraph", "First Paragraph. Body text is justified, "
             "with 1.15 line spacing and 6 pt after each paragraph."),
        para("BodyText", "Body Text."),
        '<w:p><w:pPr><w:pStyle w:val="BodyText"/></w:pPr><w:r>'
        '<w:rPr><w:rStyle w:val="VerbatimChar"/></w:rPr>'
        "<w:t>Verbatim Char</w:t></w:r><w:r><w:t xml:space=\"preserve\">"
        " and </w:t></w:r><w:r><w:rPr><w:rStyle w:val=\"Hyperlink\"/>"
        "</w:rPr><w:t>Hyperlink</w:t></w:r></w:p>",
        para("BlockText", "Block Text."),
        '<w:p><w:pPr><w:pStyle w:val="TableCaption"/></w:pPr><w:r>'
        '<w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Table 1. </w:t>'
        '</w:r><w:r><w:t>Table Caption.</w:t></w:r></w:p>',
        sample_table(),
        para("TableNote", "Table Note. Abbreviations, symbols and sources."),
        para("CaptionedFigure", "[Figure]"),
        '<w:p><w:pPr><w:pStyle w:val="ImageCaption"/></w:pPr><w:r>'
        '<w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Figure 1. </w:t>'
        '</w:r><w:r><w:t>Image Caption.</w:t></w:r></w:p>',
        para("DefinitionTerm", "Definition Term"),
        para("Definition", "Definition"),
        para("Bibliography", "[1] A. Author, “Bibliography entry,” "
             "Journal, vol. 1, pp. 1–10, 2026."),
    ])
    sect = (f'<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter1"/>'
            '<w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>'
            f'<w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
            f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" '
            f'w:bottom="{MARGIN}" w:left="{MARGIN}" w:header="709" '
            'w:footer="709" w:gutter="0"/></w:sectPr>')
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}{sect}'
            "</w:body></w:document>")


def footer_xml():
    field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r>'
             '<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
             '<w:r><w:t>1</w:t></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            f'<w:ftr xmlns:w="{W}" xmlns:r="{R}"><w:p><w:pPr>'
            f'<w:pStyle w:val="Footer"/></w:pPr>{field}</w:p></w:ftr>')


FOOTER_REL = ('<Relationship Id="rIdFooter1" Type="http://schemas.'
              'openxmlformats.org/officeDocument/2006/relationships/footer" '
              'Target="footer1.xml"/>')
FOOTER_TYPE = ('<Override PartName="/word/footer1.xml" ContentType='
               '"application/vnd.openxmlformats-officedocument.'
               'wordprocessingml.footer+xml"/>')


def build(base: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(base)) as old:
        parts = {name: old.read(name) for name in old.namelist()}
    text = {name: parts[name].decode() for name in (
        "[Content_Types].xml", "word/_rels/document.xml.rels",
        "word/theme/theme1.xml")}
    rels = re.sub(r'<Relationship Id="rId(?:Footer1|30)"[^>]*/>', "",
                  text["word/_rels/document.xml.rels"])
    rels = rels.replace("</Relationships>", FOOTER_REL + "</Relationships>")
    types = text["[Content_Types].xml"].replace(FOOTER_TYPE, "").replace(
        "</Types>", FOOTER_TYPE + "</Types>")
    # Theme fonts back anything that names no font (e.g. Word's own UI).
    theme = re.sub(r'(<a:(?:major|minor)Font><a:latin typeface=")[^"]*',
                   rf"\g<1>{FONT}", text["word/theme/theme1.xml"])
    parts.update({
        "[Content_Types].xml": types.encode(),
        "word/_rels/document.xml.rels": rels.encode(),
        "word/theme/theme1.xml": theme.encode(),
        "word/styles.xml": styles_xml().encode(),
        "word/document.xml": document_xml().encode(),
        "word/footer1.xml": footer_xml().encode(),
    })
    order = ["[Content_Types].xml"] + sorted(
        name for name in parts if name != "[Content_Types].xml")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as new:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            new.writestr(info, parts[name])
    return out.getvalue()


def main(argv):
    if len(argv) not in (2, 3) or argv[2:] not in ([], ["--check"]):
        raise SystemExit(__doc__.strip().splitlines()[-1])
    path = Path(argv[1])
    current = path.read_bytes()
    built = build(current)
    if argv[2:] == ["--check"]:
        if built != current:
            raise SystemExit(f"{path} is stale; run make template-reference")
        print(f"Reference document up to date: {path}")
    elif built != current:
        path.write_bytes(built)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main(sys.argv)
