---
figureTemplate: "**$$figureTitle$$ S$$i$$.** $$t$$"
refIndexTemplate:
  fig: "S$$i$$$$suf$$"
  default: "$$i$$$$suf$$"
figPrefix: "Figure"
---

::: {#fig:panels .wide}
![First *panel*](x.png){#fig:pa width=40%}

![Second panel](x.png){#fig:pb}

![Third panel](x.png){#fig:pc}

![](x.png){#fig:pd}

Combined *caption*
:::

::: {#fig:nocap}
![One](x.png){#fig:n1}

![Two](x.png)
:::

::: {#fig:text}
Some text.

![Img](x.png){#fig:t1}

Caption here.
:::

R [@fig:pa; @fig:pb; @fig:pc]. R2 [@fig:pc; @fig:panels]. R3 -@fig:pb. R4 [@fig:pa, p. 2]. R5 [@fig:n1; @fig:nocap; @fig:t1; @fig:text].
