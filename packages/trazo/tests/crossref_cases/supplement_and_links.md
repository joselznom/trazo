---
figureTemplate: "**$$figureTitle$$ S$$i$$.** $$t$$"
tableTemplate: "**$$tableTitle$$ S$$i$$.** $$t$$"
refIndexTemplate:
  fig: "S$$i$$$$suf$$"
  tbl: "S$$i$$$$suf$$"
  default: "$$i$$$$suf$$"
figPrefix: "Figure"
secPrefix: "Section"
---

# Head {#sec:h}

![A see [@tbl:x]](x.png){#fig:a}

![B](x.png){#fig:b}

![C](x.png){#fig:c}

: X {#tbl:x}

| a |
|---|
| 1 |

U1 ([@fig:a]). U2 [@fig:a; @fig:b; @fig:c]. U3 [see @fig:a; cf @fig:b]. U4 -@fig:b. U5 [@fig:a, p. 2]. U6 [@sec:h]. U7 x[@fig:a]y. U8 [@fig:a; @fig:missing].
