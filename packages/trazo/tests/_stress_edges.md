# Edge cases {#edge-cases}

This chapter holds the cases a generated chapter does not: each paragraph
says what it exercises. Values: {{v:edge.count}} records, a formatted
composite {{v:edge.score|mean_sd}}, and inline code that stays literal:
`{{v:no.such.key}}` and `[@fig:nowhere]`.

## Análisis *de* señal: São Paulo – 東京 {#sec:edge-unicode}

Accented and non-Latin prose: naïve façade coöperation, Ελλάδα, Øresund, Kraków,
emoji 😀🧪, combining e&#769; and é, Arabic مرحبا, and a non-breaking
space before 5 mg. A citation key with accented letters [@müller2019ångström]
and keys with punctuation [@doe:2020-a; @smith_2021.b; @o-brien2018].

```text
A fenced block keeps {{v:also.missing}} and [@tbl:missing]
exactly as written.
!include does/not/exist.md
```

## Repeated text

The same sentence appears in several paragraphs of this chapter.
Its first copy opens this paragraph.

Here is a second paragraph. The same sentence appears in several
paragraphs of this chapter. Only this copy has a comment.

The same sentence appears in several paragraphs of this chapter.

## Cross-references

A forward reference to [@fig:edge-late] and to [@eq:edge-sum], a group
of mixed kinds [@fig:edge-late; @tbl:edge-wrapped; @eq:edge-sum], one
with a suffix [@tbl:edge-wrapped, last row], and one in parentheses
([@tbl:edge-nested]).

Table: [A caption typed by hand]{.caption-title} that wraps over
several source lines, with *emphasis*, a value {{v:edge.count}},
brackets [like these], a citation [@doe:2020-a] and a reference to
[@fig:edge-late]. {#tbl:edge-wrapped}

| Name with *emphasis* | Count | Note                        |
|:---------------------|------:|:----------------------------|
| α-ratio              |   1   | Ünïcödé, “curly” quotes     |
| pipe \| inside       |  22   | a [link](https://example.org) |
| empty cell           |       |                             |

$$
\sum_{i=1}^{n} x_i^2 = \lVert x \rVert_2^2
$$ {#eq:edge-sum}

1. An ordered list item with math $\alpha=0.05$ and a value {{v:edge.count}}.
2. A second item with a nested list:
   - a bullet with a citation [@smith_2021.b];
   - a bullet with a reference to [@eq:edge-sum].

Term defined in an ordinary definition list
:   Its definition has two paragraphs.

    The second paragraph of the definition.

> A block quote with a footnote.[^edge-quote]

[^edge-quote]: The footnote cites [@o-brien2018] and shows {{v:edge.count}}.

![[Figure with a space in its file name.]{.caption-title} The caption
has brackets [log scale], *emphasis* and a reference to
[@tbl:edge-wrapped].](<figures/output/png/edge late figure.png>){#fig:edge-late width=4in}

## Supplementary references

A supplementary figure cited through a value holding its number:
Supplementary Figure @@SUPP_FIG@@.

Cited by its label: [@@@SUPP_LABEL@@].

## Included text

!include ../shared/box.md
