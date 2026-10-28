# Translation decisions and glossary contract

Create one record per consequential ambiguity and group repeated occurrences under the same stable ID where the context supports one rule.

Each decision needs:

- source revision and exact source anchor;
- source passage and enough context to judge it;
- source and target BCP-47 locale identifiers;
- ambiguity type (name, address, idiom, humor, cultural reference, invented term, register, title, or recurring phrase);
- two or more real alternatives when applicable;
- a concise recommendation with impact and risk;
- scope of repeated use;
- explicit status and selected option.

The machine-readable ledger is authoritative. A future branded PDF is a review artifact, not proof of consent. Never interpret an opened report, empty cell, or silence as approval.

The glossary records approved names, terms, recurring phrases, and character voice constraints with the originating decision ID. A changed source passage or changed decision must mark dependent translated segments for review. PR-01 does not yet automate that invalidation.
