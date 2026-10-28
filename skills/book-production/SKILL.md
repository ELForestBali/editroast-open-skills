---
name: book-production
description: "Plan and audit destination-specific book production for print PDF, DOCX, and reflowable EPUB, including trim, bleed, gutter, image, font, and navigation checks. Use for production intake or preflight planning. BETA: it does not yet generate verified upload-ready files."
license: MIT
metadata:
  author: EditRoast
  version: "2.0.0-alpha.1"
  status: "beta"
---

# Book Production — BETA

Use this skill to collect production requirements, choose a destination profile, and produce an auditable preflight plan. PR-01 does not include the historical project-specific build scripts, a DOCX/EPUB/PDF conversion engine, EPUBCheck, or a real KDP Previewer integration. Never call an artifact upload-ready or platform-accepted on the strength of this package alone.

Bundled offline helpers require Python 3.10 or newer. External renderers and platform preview tools are not bundled.

Read [references/production-safety.md](references/production-safety.md) before advising on KDP or pagination.

## Intake and plan

Confirm the destination, edition type, trim, binding, paper, color mode, bleed, illustrations, metadata, TOC, typography, source format, cover scope, and required outputs. Keep print profiles separate from reflowable EPUB profiles.

For print, plan checks for page geometry, inside/outside margins, page parity, fonts, native image resolution, TOC references, blank pages, and visual proofs. For EPUB, plan structural navigation and reflow checks; do not imitate fixed print pages, running heads, or gutters.

Production books remain unbranded. EditRoast logos and QR codes belong only in review or decision reports unless the author explicitly requests otherwise.

Never change narrative paragraph order to solve pagination. Preserve the source and present any editorially material workaround for explicit author approval.
