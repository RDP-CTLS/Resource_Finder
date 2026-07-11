# Resource Finder

A small, self-contained web app that turns a teaching centre's catalogue of PDF resources
into something faculty can actually find things in: browse by topic, start from a task
("I'm trying to write learning outcomes"), or search across every resource and glossary
term at once.

It was built by the Centre of Teaching, Learning & Scholarship (CTLS) at Red Deer
Polytechnic, but it is deliberately generic: all the content lives in one data file, so
another institution can fork this repo, swap in their own catalogue, and have the same
finder for their resources. See "Adapt it for your institution" below.

## What a teacher gets

- **Two home views**: a toggle flips the grid between topic tiles ("Assessment &
  grading", "Designing my course", ...) and task tiles ("I'm trying to write
  learning outcomes", "build a rubric", ...), two lenses on the same collection.
  Topics carry a short "start here when you are short on time" reading path;
  tasks open a curated, ordered path through the relevant resources.
- **Search as you type** from the header on every view, across resource titles,
  summaries, key points, and a 121-term glossary.
- **Resource cards** with a one-line summary, "use this when" planning moments, a
  direct link to the PDF, and "jump to" links that open the PDF at a specific page.
- **Cross-cutting lenses**: toggles that reveal, on every card, where AI (or another
  cross-cutting theme) fits into that resource.
- **Shareable URLs**: every topic, task, resource, and search has a deep-linkable
  address (`#/topic/...`, `#/task/...`, `#/res/...`, `#/search/...`), and the browser
  back/forward buttons work.

## The provenance system (the core idea)

Every block of information on a card is colour-coded by where it came from:

| Colour | Label | Meaning |
|---|---|---|
| purple | From the resource | summarized from the PDF itself |
| green | CTLS context | written by the local teaching centre for its institution |
| teal | Outside research | added from published research, with the source linked on every note |

This is also what makes the app forkable: the purple layer is regenerated from *your*
PDFs, the green layer is *your* institutional voice, and the teal layer is general,
citable research notes.

## How it's built

Two files, no frameworks, no build step, no CDN, no external fonts (it works on
locked-down campus networks):

- **`index.html`** holds the whole app: HTML, CSS, and vanilla JS in one file.
- **`ctls-data.json`** holds the whole catalogue (`meta`, `topics`, `resources`,
  `tasks`, `glossary`, `edges`). The app `fetch()`es it at load and renders entirely
  from it; updating content never requires touching `index.html`.
- **`ctls-data.js`** is a `window.CTLS = {...}` mirror kept only as a `file://`
  fallback (some browsers block `fetch()` of a local file). Over http (GitHub Pages,
  `python3 -m http.server`) the JSON is the source of truth.

### Editing content (Phase 0 inline editor)
`index.html` includes a per-card editor that is **hidden from readers**: it appears only
when the page is opened with `?edit` in the URL, or via a per-card deep link
`#/edit/<slug>` (which self-enables edit mode). The plain Finder URL has no edit chrome.

In edit mode, a pencil on each card opens an inline, provenance-grouped form with live
preview, validation (required fields, well-formed URLs, no uncited research), add/retire,
and a **Download updated data (JSON)** button.

Saving is deliberately powerless: the editor **cannot write to the site**. To publish a
change, download the updated `ctls-data.json` and have a repo owner commit it (Pages then
redeploys). The intended front door is a staff-only SharePoint page of `#/edit/<slug>`
links (behind institutional login) — that page's permissions decide who may propose an
edit; the owner's commit is the publish step. No credentials live in the page, so a
shared/forwarded link leaks nothing. (Phase 1 upgrade: a "Sign in with GitHub" step so an
editor can save directly as a Pull Request, skipping the owner handoff.)

## Run it locally

```
python3 -m http.server 8731
# then open http://localhost:8731/index.html
```

Serve it over http (not a bare `file://` open) so the app loads `ctls-data.json`;
hard-reload after editing the data.

## Adapt it for your institution

1. **Fork the repo** and replace the contents of `ctls-data.json` with your catalogue
   (regenerate the `ctls-data.js` fallback mirror to match). The object has five sections:
   - `topics`: `{name, id, blurb, state, startHere: [resource titles], icon}`
   - `resources`: `{title, topic, status, oneLine, useWhen: [strings], readTime,
     format, poly (your institutional angle), ai (where AI fits), related: [titles],
     concepts: [key points], url (link to the PDF), anchors: [{label, page}],
     research: [{fact, source, url}]}`
   - `tasks`: `{id, label, intro, steps: [{res: resource title, note}]}`
   - `glossary`: `{term, def, sources: [resource titles]}`
   - `edges`: resource-to-resource relationships (loaded but not currently rendered)
2. **Rewrite the green-layer text in your voice**: the `poly` and `ai` fields,
   `useWhen` lines, task labels, and the site name in the header/footer of
   `index.html`.
3. **Restyle if you like**: all colours are CSS custom properties in the `:root`
   block at the top of `index.html`.
4. **PDF page anchors**: the "jump to" links append `#page=N` to the resource URL,
   which works when your PDFs are served with `Content-Disposition: inline`
   (browser-rendered), not as forced downloads. Verify your page numbers against the
   actual PDFs before shipping them.
5. **Deploy**: it's a static site; any static host works, including GitHub Pages
   (publish the repo root).

## License

MIT (see LICENSE) for everything in this repo: the app and the catalogue data file.
The resource PDFs themselves are not part of this repo; they are linked in place on
rdpolytech.ca and remain Red Deer Polytechnic's. If you fork this, your catalogue's
PDFs stay wherever (and however licensed) you host them.

## Notes on this copy (Red Deer Polytechnic)

The catalogue holds RDP's 17 Teaching Development resources plus a 121-term glossary,
distilled from a full read of the source PDFs. Some resources still reference the
retiring LMS (Blackboard); they carry an automatic "Brightspace update" chip until
their rewritten versions land, which is a deliberate choice to keep cards truthful to
the PDFs they link to. Outside-research notes were added July 2026; all cited URLs
were verified live. Questions: CTLS@rdpolytech.ca.
