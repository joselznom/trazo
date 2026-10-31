| A | B |
|---|---|
| 1 | 2 |

: Plain table {#tbl:plain}

::: {#tbl:div}
| A | B |
|---|---|
| 1 | 2 |

: Div table caption
:::

::: {#tbl:not-alone}
Text before.

| A |
|---|
| 1 |

: Not alone
:::

::: {#tbl:outer}
| A |
|---|
| 1 |

: Has its own label {#tbl:inner}
:::

See [@tbl:div; @tbl:plain], [@tbl:not-alone], [@tbl:outer], [@tbl:inner].
