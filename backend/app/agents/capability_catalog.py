from __future__ import annotations

"""
Declarative technical capability catalog used by Agents to discover data schema,
Python analytical tools, and D3.js v7 layout primitives dynamically WITHOUT
hardcoding prompt-to-template mappings.
"""

DATASET_SCHEMA_GUIDE = """
OpenAlex Works JSON record structure (`records.json` is a JSON list of work objects):
- `id`: OpenAlex work URL string (e.g. "https://openalex.org/W...")
- `doi`: DOI URL or null
- `title`: paper title string
- `publication_year`: integer (2018..2025)
- `publication_date`: ISO date string
- `type`: e.g. "article", "preprint", "review", "book-chapter"
- `cited_by_count`: integer >= 0
- `is_oa`: boolean or null
- `primary_topic`: object or null:
  {
    "id": "...", "display_name": "<topic name>", "score": float,
    "subfield": {"id": "...", "display_name": "<subfield>"},
    "field": {"id": "...", "display_name": "<field>"},
    "domain": {"id": "...", "display_name": "<domain>"}
  }
- `topics`: list of topic objects (each with display_name, score, subfield, field, domain)
- `concepts`: list of {"id", "display_name", "level", "score"}
- `authorships`: list of authorship dicts:
  {
    "author_position": "first" | "middle" | "last",
    "author": {"id": "...", "display_name": "<author name>", "orcid": "..."},
    "institutions": [{"id": "...", "display_name": "<inst name>", "country_code": "US", "type": "education"}],
    "countries": ["US", ...]
  }
- `referenced_works_count`: integer
- `referenced_works`: list of OpenAlex work IDs cited by this paper
- `venue`: {"id", "display_name", "type"} or null
"""

PYTHON_SANDBOX_CAPABILITIES = """
Sandbox Python 3.13 environment (`python -I`, offline, no network/subprocess):
Available libraries: `json`, `sys`, `math`, `collections`, `itertools`, `statistics`, `pathlib`,
`pandas`, `numpy`, `networkx`, `scipy`.
Script contract:
- `sys.argv[1]` is the absolute input JSON file path.
- `sys.argv[2]` is the absolute output JSON file path to write.
- Must write valid UTF-8 JSON to `sys.argv[2]` (use standard Python `int`/`float`/`str`/`list`/`dict` — convert numpy/pandas scalars before `json.dump`).
NetworkX tips:
- Use `networkx.algorithms.community.greedy_modularity_communities(G)` or `louvain_communities` for real community detection.
- Keep graph visualizations readable: select top active authors/nodes (e.g. 35-70 nodes with real co-authorship/citation edges, plus degree/centrality/papers/citations metadata) so node-link or bundled layouts stay informative rather than unreadable hairballs.
Hierarchical / Bundling tips:
- For hierarchical edge bundling or taxonomic views, build a genuine multi-level hierarchy from OpenAlex metadata (e.g., `root -> field/subfield -> primary_topic -> entity/author/paper` or `root -> country/institution -> author` or `root -> topic_group -> topic`) and derive cross-branch bundle edges from real co-occurrence across papers (`topics` co-occurring on the same paper, co-authorships across subfields/institutions, or cross-topic citations) with real edge weights! Never invent synthetic random coordinates.
"""

D3_V7_CAPABILITY_GUIDE = """
Frontend Visualization Environment:
- Local D3 v7.9.0 is served at exact script tag: `<script src="/vendor/d3.v7.min.js"></script>` (NO external CDNs!).
- Exact data JSON computed by AnalysisAgent will be embedded inside:
  `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>`
  Your JS MUST parse data via:
  `const DATA = JSON.parse(document.getElementById('analysis-data').textContent);`
  and inspect ONLY the exact keys documented in the Analysis Output Schema provided to you.
- D3 v7 API reminders (avoid v3/v4/v5 obsolete syntax):
  - Event callbacks receive `(event, d)`, NOT `(d)` with `d3.event`! Use `event.pageX`, `event.clientX`, `d3.pointer(event)`.
  - Zoom: `const zoom = d3.zoom().scaleExtent([0.3, 6]).on('zoom', (event) => g.attr('transform', event.transform)); svg.call(zoom);`
  - Drag on force nodes: `d3.drag().on('start', ...).on('drag', ...).on('end', ...)` setting `d.fx`/`d.fy`.
  - Force layout: `d3.forceSimulation(nodes).force('link', d3.forceLink(links).id(d => d.id).distance(...)).force('charge', d3.forceManyBody().strength(...)).force('collide', d3.forceCollide().radius(...)).force('center', d3.forceCenter(w/2, h/2))`.
    Important: Run `for (let i = 0; i < 180; i++) simulation.tick();` synchronously on initial render (or tick + live simulation with low alpha) so the chart already has clean, non-overlapping positions within < 1s for headless validation & instant user responsiveness!
  - Hierarchical Edge Bundling in D3 v7:
    - Build hierarchy with `const root = d3.hierarchy(DATA.hierarchy);` (or stratify), assign radial layout `d3.cluster().size([2 * Math.PI, radius])(root)`.
    - Always check that EVERY node has finite `d.x` and `d.y` (note: `root.y` is `0` at center; never divide by `d.y`, and never pass collapsed nodes without `x`/`y` into `lineRadial()`).
    - Map leaves by ID/name: `const map = new Map(root.leaves().map(d => [d.data.id, d]));`
    - For each edge `{source, target, weight}`, resolve `const s = map.get(edge.source), t = map.get(edge.target); if (!s || !t) continue; const path = s.path(t);` and ONLY render if `path.every(n => Number.isFinite(n.x) && Number.isFinite(n.y))`, using `d3.lineRadial().curve(d3.curveBundle.beta(tension)).radius(d => d.y).angle(d => d.x)`!
    - Place radial leaf labels cleanly rotated (`rotate(...) translate(...)`, flipped if angle >= Math.PI), with ample SVG margin so zero labels clip outside the viewport!
    - Highlight connected incoming/outgoing bundle paths + neighbor labels on hover/click, and expose interactive tension (`beta`) slider + group/edge-weight filter!
  - Statistical / Matrix / Heatmap / Treemap visualizations:
    - Use `d3.scaleBand()`, `d3.scaleLinear()`, `d3.scaleSequential(d3.interpolateYlGnBu)` or `d3.interpolateViridis`, clean gridlines, interactive tooltips positioned within viewport bounds, interactive metric/year/grouping controls, and responsive `viewBox` (`preserveAspectRatio="xMidYMid meet"`).
- Layout & Polish Standards:
  - Light clean academic theme (`#ffffff` / `#f8fafc` surface, `#0f172a` ink, crisp typography, clear legend).
  - Place the main D3 `<svg>` visualization card FIRST at the very top of the page, and place summary KPI grids, statistical tables, headlines, and interactive toolbars/controls BELOW the chart container (leaving `padding-bottom: 84px` on `body` for the floating dock).
  - Never allow `NaN` in any SVG attribute (`x`, `y`, `cx`, `cy`, `d`, `width`, `height`, `transform`). Guard against empty arrays or 0-division (`|| 1`).
  - SVG MUST fill a generous visual area (e.g. `min-height: 460px`, `width: 100%`, `viewBox="0 0 1160 660"`).
"""

IFRAME_VIEW_STATE_BRIDGE = """
<script data-rc-bridge="1">
(function () {
  if (window.__rcBridgeInstalled) return;
  window.__rcBridgeInstalled = true;

  function collectFilterState() {
    var state = {};
    var controls = document.querySelectorAll('input, select');
    controls.forEach(function (el, idx) {
      var key = el.id || el.name || ('ctrl_' + idx);
      if (el.type === 'checkbox' || el.type === 'radio') {
        state[key] = !!el.checked;
      } else {
        state[key] = el.value;
      }
    });
    return state;
  }

  function applyFilterState(fs) {
    if (!fs || typeof fs !== 'object') return;
    var controls = document.querySelectorAll('input, select');
    controls.forEach(function (el, idx) {
      var key = el.id || el.name || ('ctrl_' + idx);
      if (!(key in fs)) return;
      if (el.type === 'checkbox' || el.type === 'radio') {
        el.checked = !!fs[key];
      } else {
        el.value = String(fs[key]);
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  function describeElementAt(xNorm, yNorm) {
    try {
      var x = Math.max(0, Math.min(window.innerWidth - 1, xNorm * window.innerWidth));
      var y = Math.max(0, Math.min(window.innerHeight - 1, yNorm * window.innerHeight));
      var el = document.elementFromPoint(x, y);
      if (!el) return null;
      var parts = [el.tagName.toLowerCase()];
      if (el.id) parts.push('#' + el.id);
      if (el.classList && el.classList.length) {
        parts.push('.' + Array.from(el.classList).slice(0, 2).join('.'));
      }
      var txt = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 48);
      if (txt) parts.push('("' + txt + '")');
      return parts.join('');
    } catch (e) {
      return null;
    }
  }

  window.addEventListener('message', function (event) {
    var msg = event && event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'RC_REQUEST_VIEW_STATE') {
      var hint = (typeof msg.x_norm === 'number' && typeof msg.y_norm === 'number')
        ? describeElementAt(msg.x_norm, msg.y_norm)
        : null;
      var payload = {
        type: 'RC_VIEW_STATE',
        request_id: msg.request_id || null,
        element_hint: hint,
        preview_view_state: {
          viewport_width: window.innerWidth || 1280,
          viewport_height: window.innerHeight || 720,
          scroll_x: window.scrollX || 0,
          scroll_y: window.scrollY || 0,
          zoom: 1,
          filter_state: collectFilterState()
        }
      };
      try {
        window.parent.postMessage(payload, '*');
      } catch (e) {}
    } else if (msg.type === 'RC_APPLY_VIEW_STATE') {
      applyFilterState(msg.filter_state);
    }
  });
})();
</script>
"""
