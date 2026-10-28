# Use the workflow in an ordinary AI chat

An ordinary browser chat may read uploaded files, but it usually cannot run this repository's Python commands, maintain a local workspace, or reliably carry an entire long book across many separate turns. Do not upload the ZIP and assume the chat will execute the full workflow.

Use this manual method when Codex or Claude Code is unavailable.

Literary translation and book production are BETA planning foundations in PR-01. An ordinary chat cannot turn them into a verified full-book translation, corrected DOCX, EPUB, print PDF, or KDP-accepted file. Use the local installed path for decision records and deterministic checks, and keep those limitations explicit.

## Prepare the manuscript yourself

1. Keep an untouched copy of the original.
2. Split the manuscript into clearly named chapter files such as `ch001.md`, `ch002.md`, and `ch003.md`.
3. Create a `book-map.md` file using `templates/book-map.md`.
4. Create a simple checklist containing every chapter name.

## Full Manuscript Roast

1. Upload the chapter list and, if the product allows it, all chapter files.
2. Ask the model to inventory the whole book before making final claims.
3. Build the book map in batches. After each batch, download or copy the updated map.
4. For each chapter, ask for a report with exact short quotes and chapter references.
5. Verify every quote against your original before accepting the finding.
6. Mark the chapter complete on your checklist.
7. In a final conversation, provide the completed book map and chapter reports and ask for a prioritized revision plan.

## Full Manuscript Proofreading

1. Create a style sheet for language, dialect, names, terms, capitalization, punctuation, and intentional voice choices.
2. Process chapters in order. Carry the updated style sheet into every new chat.
3. Require two separate lists: objective corrections and optional style suggestions.
4. Require the exact original phrase and enough surrounding context to find it.
5. Check every suggestion against the source. Do not use bulk replace without reviewing each occurrence.
6. Keep a checklist so no chapter is skipped.

## Resume safely

Save the latest book map or style sheet, the chapter checklist, and every chapter report outside the chat. On the next session, upload those files and continue from the first unchecked chapter.

## Limits and privacy

- File size, context, message, and rate limits vary by product and plan.
- A subscription is not the same as API credit.
- Your manuscript is sent to the AI provider whose chat you use. Review its privacy settings and terms before uploading confidential work.
- Manual chat cannot provide the same deterministic completeness and quote validation as the local workflow.

