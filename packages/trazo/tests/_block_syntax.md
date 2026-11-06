# Awkward syntax {#sec:awkward}

Plain sentence with *emphasis*, **strong text**, ***both at once***, and _underscored emphasis_ in the middle of it all.

Dashes -- en and --- em, ellipsis... and "double" plus 'single' quotes, it's an apostrophe; 5 % of 10 km².

A [plain link](https://example.org/a_b?c=1) and a [link with *emphasis* inside](https://example.org "title") and an autolink <https://example.org/x> here.

A reference link [to somewhere][ref] and a shortcut one [ref] stay as they are in this sentence.

[ref]: https://example.org/reference

Chemistry H~2~O and powers 10^3^ with [small caps]{.smallcaps} and [a span]{.custom key="v"} in one line of text.

Inline note here^[This note has *emphasis* and [@smith2020].] and the sentence goes on after the note.

Hard break at the end of this line\
and the next line follows, then two spaces here  
and a third one closes the paragraph.

Escapes \*not emphasis\*, 3 \< 5, a \$5 price, \[not a link\], a\_b and C:\\path in the prose.

Entities &alpha; and &#233; and a non-breaking~space in the running text of the paragraph.

An inline comment <!-- a note to self --> sits between these words and must survive any edit here.

Math $E = mc^2$ next to words, $\alpha$ twice $\beta$, and code `x_1 * y` with `{{v:not.a.key}}` beside them.

Citations in all forms: @smith2020 says so, [@smith2020, p. 3], [see @smith2020; @smith2020], [-@smith2020] and a bare @smith2020 too.

Cross-references: [@tbl:example], @tbl:example, [Section @sec:awkward] close together.

A value {{v:cohort.n}} and a format {{v:cohort.n}} next to *emphasised {{v:cohort.n}} value* in the text.

Raw HTML <span style="color:red">red words</span> and <sub>sub</sub> and raw TeX \LaTeX{} and \ref{x} inline here.

Line one of a block with
a hard-wrapped second line that ends in *emphasis
across the wrap* and then a third line
with the end of the sentence.

> A block quote paragraph with *emphasis* and [@smith2020] that
> continues on a second quoted line of source.

1. First item with *emphasis*.
2. Second item with a citation [@smith2020].

   A second paragraph inside the second item, indented.

- Bullet one [@smith2020].
- Bullet two
  continues on a wrapped line.
    - Nested bullet with `code`.

(@) An example list item.

- [ ] A task item not done.
- [x] A task item done.

Term
:   A definition with *emphasis*.

| Line blocks stay
| as they are written

Text with trailing attributes [word]{#anchor} and an image ![inline](figures/nowhere.png){height=1em} in the flow.

Text with footnote[^aw] reference then more words after it.

[^aw]: The footnote text.

::: {.custom-box}
A paragraph inside a fenced div with *emphasis*.
:::

Ünïcödé naïve café 東京 😀🧪 مرحبا e&#769; ﬁ ligature, tab	inside and double  spaces  inside the sentence.

A citation with a locator @smith2020 [p. 3] and a linked one [see @smith2020](https://example.org) stay whole.

A double tick ``a`b`` and a price of $5 and $6 are not what they seem here.

Highlighted `print(1)`{.python} and an image in parentheses ![a](a(b).png) keep their syntax.

## Quoted attribute {#sec:quoted title='hello world' .x}

## Empty attributes {}

## Literal braces {literal}

## Inline span [word]{.smallcaps}

## 1. Numbered title

(1) A first hypothesis here.
(2) A second hypothesis here.

Paragraph before a fence.
```python
keep_this_code()
```

A linked [section name](#sec:awkward) in a sentence, and [one with a class](#sec:awkward){.internal}.

Three aims follow in this study:
(1) to measure and
(2) to compare.

**[@smith2020]** in bold, and [@smith2020] - a dash after it.

> A quoted paragraph.
>
> A second quoted paragraph.

\begin{equation}
\hat{y} = \beta_0
+ \beta_1 x_1
+ \beta_2 x_2
\end{equation}

<pre>
- not a list
+ not one either
</pre>

1. An item with an equation:

   \begin{equation}
   \hat{y} = \beta_0
   + \beta_1 x_1
   \end{equation}

2. Another item.

A closing line.

II. A roman item.

III. Another one.

A.	First lettered item.
B.	Second lettered item.

1. \begin{equation}
   y
   + a
   \end{equation}
2. After the equation.

- The model is:
  \begin{equation}
  y
  + a
  \end{equation}

- First.

  <?xml
  + content
  ?>

- > <pre>
  > - raw one
  > + raw two</pre>

- An item.

  \begin{equation}
  y
  \end{equation}

  More of the item.
- The next item.

Setext title
============

Text before a rule.

---

<div class="raw">Raw block</div>

<!-- A comment alone -->

<!--
A comment on
several lines.
-->

    An indented code block stays.

```
A fenced block stays too.
```

\begin{center}
Raw TeX
\end{center}

Same words twice.

Same words twice.

| A | B |
|---|---|
| 1 | 2 |

A closing paragraph with no newline after it.