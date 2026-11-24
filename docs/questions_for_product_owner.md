# Questions for Product Owner (Stage-1 Alignment)

Per Section 2 and Section 12 of `Project1_Research_Canvas_Handoff_v1.0.md`, **all 13 core L1.5 product interaction decisions are already locked and are NOT reopened here**:
- Conversation-first -> Split Chat + Infinite Canvas (no permanent Preview, no Artifact Shelf)
- Vertical task outputs + rightward version evolution
- Validation Chromium screenshots as thumbnails
- Centered `80vw × 80vh` modal + unified `Preview | Data Map | Comments | [sep] | View Source | Download | Minimize` Dock
- Deferred Visual Comments (save locally with anchor & screenshot; only Chat triggers revision)
- Blank-canvas left-button long-press marquee -> right-click `Compare` -> dual independent Docks

## Non-Blocking Engineering Defaults (For Awareness Only)

No product-blocking ambiguities remain. We proceed using these sensible defaults (adjustable via config if desired):

1. **Blank-Canvas Long-Press vs Pan Disambiguation Threshold**:
   - **Default (Recommended)**: `320ms` stationary hold (`< 6px` movement) triggers marquee selection with a subtle visual hold ring; moving `>= 6px` immediately pans the canvas; we also include a toggleable toolbar chip / `Shift+Drag` shortcut as a secondary accessibility fallback for trackpad-only users.
2. **Download Package Format**:
   - **Default (Recommended)**: Clicking `Download` offers two clean options in a lightweight menu/default action: **(a) Standalone HTML (`.html`)** with D3 v7 inlined so the interactive chart opens offline with zero server dependency, and **(b) Provenance Bundle (`.zip`)** containing `index.html`, `analysis_result.json`, `analysis_script.py`, `lineage.json`, and `metadata.json`.
3. **OpenAlex Dataset Scope & API Budget Handling**:
   - **Default (Recommended)**: Seeded with 400 real OpenAlex AI works (`publication_year:2018-2025, concepts.id:C154945302`) containing full authorship, institution, citation, and 4-level topic hierarchy (`domain -> field -> subfield -> topic`) records. Live OpenAlex filter queries run automatically when `OPENALEX_API_KEY` or shared free quota is available, falling back transparently to the verified local OpenAlex snapshot with explicit provenance disclosure when rate-limited (HTTP 429) or offline.
