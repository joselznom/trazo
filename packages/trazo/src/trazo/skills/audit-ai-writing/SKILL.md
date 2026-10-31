---
name: audit-ai-writing
user-invocable: false
description: Use when asked to audit prose for signs of AI-generated writing (AI tells, tics, "humanize", sound less like AI) in manuscript text, reviewer responses or documentation.
---

# Audit AI Writing

Use Wikipedia's "Signs of AI writing" as a field guide, not a detector.
Never claim text is AI-generated; say a passage "has signals commonly
associated with AI-generated prose".

## Workflow

1. Read the target text and its context. Preserve Pandoc citations,
   cross-references and `{{v:key}}` placeholders.
2. Quote the exact excerpt before naming a signal.
3. Academic conventions are not signals: passive voice, structured
   Methods/Results, technical terms, required headings.
4. One isolated signal is weak. Escalate when signals cluster,
   especially when they make claims broader than the evidence.

## Signals

Content:

- Inflated significance, legacy or "broader trend" claims without
  specific evidence.
- Shallow links: a finding "underscores", "reflects" or "contributes to"
  an abstraction without the mechanism.
- Promotional tone about people, institutions, tools or projects.
- Vague attribution: "experts argue", "studies suggest", "widely
  regarded" backed by narrow or no citations.
- Formulaic "challenges" or "future directions" endings that speculate
  instead of reporting.

Language:

- Clusters of stock vocabulary: additionally, crucial, delve, enhance,
  foster, highlight (verb), interplay, intricate, landscape, meticulous,
  pivotal, robust, showcase, tapestry, testament, underscore.
- "serves as", "stands as", "represents" where "is" or "has" is meant.
- Negative parallelism ("not only X but also Y", "not X but Y") and
  repeated triplets.
- Elegant variation that swaps a precise term for synonyms.

Format and residue:

- Title case where the document uses sentence case; needless bold,
  inline-header bullets, or tables packaging ordinary prose.
- Heavy em dashes, emoji, curly quotes in plain Markdown, skipped
  heading levels.
- Tool artifacts: `turn0search0`, `contentReference`, `oaicite`,
  `[attached_file:1]`, raw UTM parameters; chatbot residue such as
  knowledge-cutoff disclaimers or "I hope this helps".

## Fixes

- Replace broad claims with the observed result, quantity or citation;
  delete unsupported importance or future-impact claims.
- Use plain verbs: is, has, uses, measured, reported, estimated.
- Name vague sources or remove them.
- Break triplets and parallelisms that do not match the logic.

## Output

- Verdict: no, low, moderate or high signal, with confidence.
- Findings: severity, exact excerpt, signal, why it matters, fix.
- Rewritten prose only when the user asks for edits or a short
  replacement is the clearest fix.
