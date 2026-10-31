---
abstract: "We show [@fig:a]."
figureTemplate: "**$$figureTitle$$ $$i$$.** $$t$$"
tableTemplate: "**$$tableTitle$$ $$i$$.** $$t$$"
figPrefix: "Figure"
tblPrefix: "Table"
secPrefix: "Section"
---

# Methods {#sec:m}

## About [@fig:a] {#sec:about}

::: {.form-only}
# Hidden {#sec:hidden}

![[Title.]{.caption-title} Legend with [@tbl:x] and [@sec:m].](x.png){#fig:a width=4.5in}
:::

Note.^[See [@fig:a; @fig:b].]

![B](x.png){#fig:b}

Table: Leg {#tbl:x}

+---+---+
| a | b |
+===+===+
| 1 | 2 |
+---+---+

::: {.table-note custom-style="Table Note"}
note [@tbl:x]
:::

- item [@fig:b]

> quote [@sec:hidden]

| a |
|---|
| 1 |

: Untagged

Refs [@fig:a; @fig:b; @tbl:x; @sec:m; @sec:about; @sec:hidden] *emph [@fig:a]* **[@fig:b]**.
