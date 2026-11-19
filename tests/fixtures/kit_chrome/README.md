`context.json`, `locked.json` and `dom_blocked.json` preserve actual supported tool
response envelopes captured read-only. They contain no real email body.

`supported_editor.json` is an anonymized fixture using the exact supported structure
observed on 16 November 2026: Kit clean content-tree paragraphs/links, a scoped
accessibility editor subtree and get_page_text's main-element envelope. The subject,
wording, URL, address and IDs are synthetic. The actual reader was separately verified
against the real permitted observations without copying that private body into tests.
