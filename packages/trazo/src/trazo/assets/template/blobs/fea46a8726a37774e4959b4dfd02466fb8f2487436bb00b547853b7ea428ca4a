---
name: add-reference
user-invocable: false
description: Use when the user asks to add a reference to bibliography/references.bib from a DOI, PubMed ID, URL or a source they name.
argument-hint: <DOI, PMID or URL>
---

# Add Reference

Add only sources the user gave or approved. Never fill a BibTeX entry
from memory.

1. Fetch the metadata from the identifier:

   ```bash
   curl -sLH "Accept: application/x-bibtex" https://doi.org/<DOI>
   ```

   For a PMID, get its DOI from
   `https://pubmed.ncbi.nlm.nih.gov/<PMID>/`. For a source without a DOI
   (software, web page), build the entry from the page itself and say
   which fields you could not confirm.
2. Check the result names the work the user meant (title, first author,
   year). If it does not, stop and ask.
3. Rename the key to the file's style, `<firstauthor><year><firstword>`
   in lowercase ASCII (for example `smith2021deep`), and make sure it is
   not already used. If the same DOI is already in the file, reuse that
   key instead of adding a duplicate.
4. Keep `author`, `title`, `journal` or `booktitle`, `year` or `date`,
   `volume`, `number`, `pages` and `doi`; drop `url` when there is a DOI,
   and drop abstracts and `month` noise. Protect acronyms in titles with
   braces (`{MRI}`).
5. Append the entry to `bibliography/references.bib`, then run
   `scripts/trazo build --prepare=inputs`, then `scripts/trazo citations`.

Report the key added so it can be cited as `[@key]`.
