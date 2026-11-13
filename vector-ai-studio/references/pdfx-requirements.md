# PDF/X requirements — sourced ledger

Status: **key clauses sourced; full ISO text not purchased; no file-level conformance
validator has been run.** This ledger records what the sources say, not what this
skill's output is. A produced file is never "PDF/X compliant" merely because a
check in this repo passed.

Research date: 2026-11-13. The complete ISO 15930 parts are paywalled; the
sources below are published previews, national-archive format signatures, and
vendor documentation. Direct fetches to iso.org / pdfa.org / Adobe help / loc.gov
still returned 403 or transport errors; where a page was only reachable through a
search-index extract, that is stated.

## 1. Identification keys (the most load-bearing table)

| Level | Where the identifier lives | Expected value |
|---|---|---|
| PDF/X-1a:2001 | Info `/GTS_PDFXVersion` | PDF/X-1:2001 |
| PDF/X-1a:2001 | Info `/GTS_PDFXConformance` | PDF/X-1a:2001 |
| PDF/X-1a:2003 | Info `/GTS_PDFXVersion` | PDF/X-1a:2003 |
| PDF/X-3:2002 | Info `/GTS_PDFXVersion` | PDF/X-3:2002 |
| PDF/X-3:2003 | Info `/GTS_PDFXVersion` | PDF/X-3:2003 |
| PDF/X-4 | XMP `pdfxid:GTS_PDFXVersion` | PDF/X-4 (no year) |
| PDF/X-4p | XMP `pdfxid:GTS_PDFXVersion` | PDF/X-4p (no year) |

Consequences:

- A checker that only looks for a string starting with PDF/X cannot tell the
  levels apart.
- PDF/X-1a:2001 is **not** identified by `GTS_PDFXVersion(PDF/X-1a:2001)`; the
  version key is PDF/X-1:2001 and the conformance key carries the `1a`.
- PDF/X-4 belongs in XMP. A raw-byte search for `/GTS_PDFXVersion` can miss a
  conforming X-4 file. The engine version tested here writes PDF/X-4 in the
  Info dictionary instead; the check accepts either source but reports which.

Source: ISO 15930-1:2001 preview (iTeh, 11 pp.), ISO/DIS 15930-1 and -3 drafts,
ISO 15930-4:2003 / -6:2003 / -7:2010 previews, PRONOM fmt/145, 146, 148, 157,
158, 488, 489, Adobe Technical Note #5413, ECI PDF/X FAQ (2005), PDF Association
"PDF/X: The key facts" (search extract), iText PDF/X article.

## 2. Requirement matrix

| Requirement | X-1a | X-3 | X-4 | Evidence |
|---|---|---|---|---|
| Output intent | Required; one characterized printing condition | Required; ICC required if device-independent colour is used | Required; ICC mandatory (X-4p may be external) | ISO previews §6.2.1; Adobe TN #5413; PDF Association |
| `/S /GTS_PDFX` in the intent | Required | Required | Required | Adobe TN #5413 |
| Fonts | All used fonts embedded, subsets contain used glyphs | Same | Same; PDF 1.6 permits OpenType | ISO 15930-1/-4/-6/-7 previews; iText; ECI |
| Encryption | Prohibited (plain PDF/X-1:2001 is the exception) | Prohibited | Prohibited | ISO 15930-1 intro; PRONOM fmt/145; LoC FDD |
| Colour | DeviceGray / DeviceCMYK / Separation / DeviceN; no RGB, Lab, ICCBased | Plus CalGray/CalRGB/Lab/ICCBased and DeviceRGB only with an RGB intent | Plus NChannel, ICC v4, 16-bit; DeviceRGB only with an RGB intent | ISO previews; PDF Association; Esko; ECI |
| Transparency | Forbidden | Forbidden | Allowed under PDF 1.6 rules | ISO 15930-4/-6/-7 intros; callas |
| Layers / OCGs | Forbidden | Forbidden | Allowed | ISO 15930-7 intro; callas |
| Boxes | MediaBox required; TrimBox **or** ArtBox required (not both); BleedBox optional and outside TrimBox | Same | Same | iText; PDF Association; ECI |
| Metadata | Title, dates, `Trapped` True/False, document ID | Same | XMP: title, dates, `pdf:Trapped`, document IDs, version/rendition | ISO previews; iText; AutomatizarPDF |
| PDF version | 1.3 (:2001) / 1.4 limited (:2003) | 1.3 (:2002) / 1.4 limited (:2003) | 1.6 | ISO 15930-7 Table 1; PRONOM notes header may vary |
| External content / OPI | :2001 file refs but no OPI; :2003 no file refs | No file refs | No external files (X-4p: external ICC only) | ISO previews; PDF Association |

## 3. What this repo's checks actually measure

| Check | Measures | Gap |
|---|---|---|
| `../scripts/land_craft.py` `_pdf_facts` | Info `/GTS_PDFXVersion` / `/GTS_PDFXConformance` literals, `/OutputIntent` object count, box rectangles | Presence/count only; no `/S /GTS_PDFX`, no ICC `/N`, no box nesting |
| `../scripts/smoke_ai.py --print` | Exact GTS pair for X-1a (after r83), output-intent count, DeviceRGB token count, content `k`/`K` | Still a structural smoke test, not conformance |
| `../scripts/pdf_preflight.py --require-pdfx --expect-standard` | Exact identification per level + GTS/OutputIntents/encryption/font heuristic | Page-1 font heuristic; no ICC validity; no transparency/layers/metadata |
| `../scripts/pdf_preflight.py` cross-check | Pages / page size / images / font presence / output-intent presence via PyMuPDF | Parser agreement, not compliance |

## 4. Still unverified (do not claim)

- Conformance of any produced file; no Acrobat Preflight, callas pdfToolbox or
  veraPDF run has been recorded.
- Whether X-1a:2003 and X-3:2003 require `GTS_PDFXConformance` in addition to
  the version key.
- Whether exactly one OutputIntent is mandatory in every part.
- Whether DeviceRGB is permitted under an RGB output intent in every X-4 case.
- Whether the engine's OutputIntent embeds a valid CMYK ICC profile whose colour
  space matches the content.
- Whether all fonts on all pages are embedded with all used glyphs.
- Whether the file has no `/Encrypt` dictionary (only "no password required" is
  checked).
- Whether X-1a/X-3 contain no transparency/OCGs; whether X-4 uses only
  permitted transparency constructs.
- Whether metadata (`Trapped`, dates, document ID, XMP for X-4) is complete.
- Whether boxes nest correctly.
- Whether there are JavaScript actions, form fields, print-area annotations,
  external references, LZW streams or transfer curves.
- Whether the file is suitable for a specific press.

## 5. Source access log

| Source | URL | Status (2026-11-13) |
|---|---|---|
| ISO 15930-1:2001 preview (iTeh) | https://cdn.standards.iteh.ai/samples/29061/baa5b934475947e8a8ebc601fd87918c/ISO-15930-1-2001.pdf | 200, 11-page preview |
| ISO/DIS 15930-1 draft | https://cdn.standards.iteh.ai/samples/38186/260736dfdfb849c99150e763501558d4/ISO-DIS-15930-1.pdf | 200, 11 pages |
| ISO/DIS 15930-3 draft | https://cdn.standards.iteh.ai/samples/38187/1a80e51b1df1453d850841c80eb045ef/ISO-DIS-15930-3.pdf | 200, 11 pages |
| ISO 15930-4:2003 preview | https://cdn.standards.iteh.ai/samples/39938/ce6abceebcbb4f88bbe65670a9776c8e/SIST-ISO-15930-4-2005.pdf | 200, 11 pages |
| ISO 15930-6:2003 preview | https://cdn.standards.iteh.ai/samples/39940/4f0c044c203543e29f671d3b9b043985/ISO-15930-6-2003.pdf | 200, 11 pages |
| ISO 15930-7:2010 preview | https://cdn.standards.iteh.ai/samples/55843/22387ef2259840d4a53f02edbd4faf30/ISO-15930-7-2010.pdf | 200, 12 pages |
| Adobe Technical Note #5413 | https://printtechnologies.org/standards/files/technote5413.pdf | 200 |
| ECI / Global Graphics PDF/X FAQ | https://eci.org/lib/exe/pdfx_faq_english_nov05.pdf | 200, 24 pages |
| PRONOM fmt/145, 146, 148, 157, 158, 488, 489 | https://pronom.nationalarchives.gov.uk/fmt/145 (etc.) | 200 |
| Library of Congress FDD | https://www.loc.gov/preservation/digital/formats/fdd/fdd000124.shtml | direct 403; search extract |
| PDF Association key facts | https://pdfa.org/pdfx-the-key-facts/ | direct 403; search extract |
| iText PDF/X article | https://mvalvekens.be/blog/2023/itext-pdfx.html | 200 |
| ISO catalogue pages | https://www.iso.org/standard/39962.html (etc.) | 403 |

## 6. How to use this ledger

1. Quote a requirement only with its source row.
2. Say "the file contains X" when that is what a check measured; do not say
   "the file is PDF/X".
3. Before any printer-facing claim, run an external validator (Acrobat
   Preflight, callas pdfToolbox, veraPDF) and keep its report; that report is
   evidence about that file, not a guarantee for a different press.
