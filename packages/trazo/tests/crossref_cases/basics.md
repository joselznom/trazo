---
figureTitle: "Figure"
tableTitle: "Table"
figPrefix: "Figure"
tblPrefix: "Table"
secPrefix: "Section"
figureTemplate: "**$$figureTitle$$ $$i$$.** $$t$$"
tableTemplate: "**$$tableTitle$$ $$i$$.** $$t$$"
numbersections: false
---

# Intro {#sec:intro}

See [@fig:a], [@fig:a; @fig:b], [@fig:a; @fig:b; @fig:c], -@fig:b, [@tbl:t], [@sec:intro], [@sec:sub], [@fig:missing], @fig:a, [@fig:a; @tbl:t], [see @fig:a, p. 3], [@fig:c; @fig:a], [@sec:intro; @sec:sub].

## Sub {#sec:sub}

![A *caption*](x.png){#fig:a}

![B caption](x.png){#fig:b width=50%}

![C caption](x.png){#fig:c}

| a | b |
|---|---|
| 1 | 2 |

Table: Tab *caption* {#tbl:t}

# Unnumbered {.unnumbered}

# Third
