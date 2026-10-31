---
figPrefix: [fig., figs.]
tblPrefix:
  - "table"
  - "tables"
---

## Pre {#sec:pre}

# Intro {#sec:intro}

### Deep {#sec:deep}

# Unn {#sec:unn .unnumbered}

## UnnSub {#sec:unnsub}

# Third {#sec:third}

## T1 {#sec:t1}

![A](x.png){#fig:a}

![B](x.png){#fig:b}

![C](x.png){#fig:c}

![D](x.png){#fig:d}

![E](x.png){#fig:e}

![nolabel](x.png)

![other](x.png){#other}

: Cap {#tbl:x}

| a |
|---|
| 1 |

Two [@fig:a; @fig:b]. Three [@fig:a; @fig:b; @fig:c]. Gap [@fig:a; @fig:c; @fig:d; @fig:e]. Dup [@fig:a; @fig:a]. Cap [@Fig:a]. Cap2 [@Fig:a; @Fig:b]. tbl [@tbl:x]. Mixed [@fig:a; @tbl:x; @fig:b]. Secs [@sec:pre; @sec:intro; @sec:deep; @sec:unn; @sec:unnsub; @sec:third; @sec:t1]. Suffix [@fig:a, left]. Supp -@fig:a and -@tbl:x. Plain @fig:b. Two suppress [-@fig:a; -@fig:b]. Mixed citation [@fig:a; @smith2020]. Prefix [see @fig:a; @fig:b].
