---
figPrefix: [fig., figs.]
---

![A](x.png){#fig:a}

![B](x.png){#fig:b}

![C](x.png){#fig:c}

![D](x.png){#fig:d}

: X {#tbl:x}

| a |
|---|
| 1 |

T1 [see @fig:a; see @fig:b]. T2 [-@fig:a; -@fig:b; @fig:c; @fig:d]. T3 [@fig:a; -@fig:b; @fig:c]. T4 [@fig:a; @fig:b; see @fig:c; @fig:d]. T5 [-@fig:a; see @fig:b]. T6 [@fig:a; @Fig:b]. T7 [@Fig:a; @fig:b]. T9 [@fig:a; @smith; @fig:b]. T10 [@smith; @fig:a]. T11 [@fig:a, p. 3; @fig:b, p. 4]. T12 [see @fig:a; see @fig:b; @fig:c]. T13 [-@fig:a; -@tbl:x]. T14 [see -@fig:a]. T15 [@fig:a; see @fig:c; see @fig:b]. T16 [@sec:nope]. T17 [@eq:nope]. T18 [@fig:c; @fig:b; @fig:a; @fig:a].
