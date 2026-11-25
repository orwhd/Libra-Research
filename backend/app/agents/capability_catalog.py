from __future__ import annotations

import importlib.metadata as importlib_metadata
import re
from typing import Any

from ..config import VENDOR_DIR

"""
Runtime-introspected capability discovery for Data, Analysis, and Visualization Agents.
Agents discover installed analytical packages and locally hosted visualization runtimes
dynamically at execution time instead of relying on hardcoded prompt-to-template mappings
or example-specific logic.
"""

DATASET_SCHEMA_GUIDE = """
Scholarly Works Record Schema (when mining OpenAlex scholarly literature):
- `id`: work identifier URL string
- `doi`: DOI URL or null
- `title` / `display_name`: work title string
- `publication_year`: integer year
- `publication_date`: ISO date string
- `type`: document type string (e.g. "article", "conference-paper", "preprint", "review", "book-chapter")
- `cited_by_count`: integer >= 0
- `is_oa`: boolean or null
- `primary_topic`: {"id", "display_name", "score", "subfield": {"display_name"}, "field": {"display_name"}, "domain": {"display_name"}} or null
- `topics`: list of topic dicts
- `concepts`: list of {"id", "display_name", "level", "score"}
- `authorships`: list of {"author_position", "author": {"id", "display_name", "orcid"}, "institutions": [{"id", "display_name", "country_code", "type"}], "countries": [...]}
- `referenced_works_count`: integer
- `referenced_works`: list of cited work IDs
- `venue`: {"id", "display_name", "type"} or null
"""

_CANDIDATE_PYTHON_LIBS: tuple[tuple[str, str], ...] = (
    ("pandas", "Tabular DataFrames, time-series resampling, joins, pivot/crosstab aggregations"),
    ("numpy", "Vectorized numerical arrays, linear algebra, quantiles, histograms, FFT"),
    ("scipy", "Statistical tests, hierarchical linkage clustering, spatial distances, curve fitting, sparse matrices"),
    ("networkx", "Graph construction, centrality metrics, community detection, shortest paths, k-core/tree structures"),
    ("scikit-learn", "Dimensionality reduction, clustering, TF-IDF, regression/classification"),
    ("statsmodels", "Time-series decomposition, OLS/GLM statistical modeling"),
)

_VENDOR_LIBRARY_METADATA: dict[str, dict[str, str]] = {
    "d3.v7.min.js": {
        "name": "D3.js v7",
        "global": "window.d3",
        "strengths": (
            "Bespoke scientific SVG/Canvas grammars, force-directed networks, hierarchical layouts "
            "(tree, cluster, treemap, pack, partition, radial edge bundling), custom scales/brushes/zoom, "
            "geographic projections, and fine-grained DOM/Canvas transitions. "
            "(Note: D3 v7 event listeners use `(event, d) => ...` and `d3.pointer(event)`.)"
        ),
    },
    "echarts.min.js": {
        "name": "Apache ECharts 5",
        "global": "window.echarts",
        "strengths": (
            "Fast, publication-ready interactive Canvas/SVG charts with built-in tooltips, dataZoom, brush, "
            "legend toggling, visualMap, multi-series line/bar/area/scatter, heatmaps, Sankey, radar, parallel "
            "coordinates, sunburst, boxplot, candlestick, and graph views (`echarts.init(dom)` + `chart.setOption(...)`)."
        ),
    },
    "chart.umd.min.js": {
        "name": "Chart.js 4",
        "global": "window.Chart",
        "strengths": (
            "Clean, responsive `<canvas>` statistical charts (line, bar, stacked area, scatter, bubble, "
            "radar, polarArea, doughnut) with crisp built-in legends and hover tooltips."
        ),
    },
}

_CDN_FALLBACK_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://[^\s\"']*d3(?:\.v7)?(?:\.min)?\.js", re.IGNORECASE), "/vendor/d3.v7.min.js"),
    (re.compile(r"https?://[^\s\"']*echarts(?:\.min)?\.js", re.IGNORECASE), "/vendor/echarts.min.js"),
    (re.compile(r"https?://[^\s\"']*chart(?:\.umd)?(?:\.min)?\.js", re.IGNORECASE), "/vendor/chart.umd.min.js"),
)


def discover_python_sandbox_capabilities() -> str:
    """Dynamically introspect installed Python analytical libraries in the sandbox environment."""
    installed_lines: list[str] = []
    for pkg_name, desc in _CANDIDATE_PYTHON_LIBS:
        try:
            ver = importlib_metadata.version(pkg_name)
            installed_lines.append(f"- `{pkg_name}` (v{ver}): {desc}")
        except importlib_metadata.PackageNotFoundError:
            continue

    stdlib_line = "- Python 3.13 standard library: `json`, `sys`, `math`, `statistics`, `collections`, `itertools`, `re`, `datetime`, `pathlib`"
    discovered_block = "\n".join([stdlib_line, *installed_lines])
    return f"""
Sandbox Python Execution Environment (`python -I`, isolated offline execution):
Discovered Available Libraries:
{discovered_block}

Execution Contract:
- `sys.argv[1]` is the input JSON file path; `sys.argv[2]` is the output JSON file path.
- Select suitable statistical, structural, clustering, temporal, or matrix algorithms dynamically based on the data schema and research question — do NOT rely on fixed templates.
- Convert all numpy/pandas scalar/array types to native Python `int`, `float`, `str`, `bool`, `list`, `dict` (no `NaN` or `Infinity`) before `json.dump`.
""".strip()


def discover_visualization_libraries() -> list[dict[str, Any]]:
    """Scan `VENDOR_DIR` dynamically to discover all locally hosted JS visualization runtimes."""
    discovered: list[dict[str, Any]] = []
    if VENDOR_DIR.exists():
        for js_file in sorted(VENDOR_DIR.glob("*.js")):
            meta = _VENDOR_LIBRARY_METADATA.get(
                js_file.name,
                {
                    "name": js_file.stem,
                    "global": "window",
                    "strengths": f"Local visualization runtime served at `/vendor/{js_file.name}`.",
                },
            )
            discovered.append(
                {
                    "filename": js_file.name,
                    "script_src": f"/vendor/{js_file.name}",
                    "name": meta["name"],
                    "global": meta["global"],
                    "strengths": meta["strengths"],
                }
            )
    return discovered


def build_visualization_capability_guide() -> str:
    """Build a dynamic capability prompt from discovered `/vendor/*.js` libraries and browser APIs."""
    libs = discover_visualization_libraries()
    lib_lines = [
        f'- `{lib["name"]}` (`<script src="{lib["script_src"]}"></script>`, exposes `{lib["global"]}`): {lib["strengths"]}'
        for lib in libs
    ]
    lib_lines.append(
        "- Native Web Graphics (`<svg>`, `<canvas>` 2D, WebGL, CSS Grid/Flex): Direct zero-dependency rendering or hybrid composite views."
    )
    libs_block = "\n".join(lib_lines)

    return f"""
Discovered Local Visualization Runtimes (choose the best-suited library or combination for the task — NO external CDN URLs):
{libs_block}

Data & Runtime Contract:
- Computed analysis JSON is embedded in `<script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>`.
- Parse `const RAW = JSON.parse(document.getElementById('analysis-data').textContent);` and inspect ONLY the exact keys documented in the provided schema.
- Ensure initial visual rendering completes synchronously or within < 500ms on `DOMContentLoaded` (e.g., pre-tick force layouts if using iterative physics; disable slow entrance delays) so `<svg>` or `<canvas>` has valid non-zero dimensions (`>= 200x150`) and finite coordinates (`no NaN/undefined`).

Layout & Safety Standards:
- Light academic surface (`#ffffff` / `#f8fafc` background, `#0f172a` ink, clear typography and color legend).
- **Chart-First Reading Order (MANDATORY)**: Place the primary interactive visualization stage (`<svg>`, `<canvas>`, or interactive chart container marked with `data-rc-chart-stage="1"`) FIRST in DOM and visual order at the very top of `<body>`. Place all filters, metric selectors, sliders, search inputs, legends, KPI summary cards, and tables AFTER / BELOW the chart stage.
- **Floating Bottom Dock Safe-Zone (MANDATORY)**: The host preview window overlays a centered floating navigation dock across the bottom ~96px (`bottom: 0..96px`). NEVER use `position: fixed` or `position: sticky` anchored near `bottom` (`0..100px`) for toolbars, legends, or footers. Keep `body {{ padding-bottom: 112px; }}` and clamp hover tooltips above `window.innerHeight - 96`.
""".strip()


def rewrite_cdn_scripts_to_local_vendor(html_code: str) -> str:
    for pattern, local_src in _CDN_FALLBACK_REWRITES:
        if (VENDOR_DIR / local_src.removeprefix("/vendor/")).exists():
            html_code = pattern.sub(local_src, html_code)
    return html_code


# Backward-compatible module constants backed by runtime discovery
PYTHON_SANDBOX_CAPABILITIES = discover_python_sandbox_capabilities()
D3_V7_CAPABILITY_GUIDE = build_visualization_capability_guide()

IFRAME_VIEW_STATE_BRIDGE = """
<script data-rc-bridge="2">
(function () {
  if (window.__rcBridgeV2Installed) return;
  window.__rcBridgeV2Installed = true;

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
      if (!(key in fs) || key.indexOf('__') === 0) return;
      if (el.type === 'checkbox' || el.type === 'radio') {
        el.checked = !!fs[key];
      } else {
        el.value = String(fs[key]);
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  function formatTagSelector(el) {
    if (!el || !el.tagName) return '';
    var parts = [el.tagName.toLowerCase()];
    if (el.id) parts.push('#' + el.id);
    if (el.classList && el.classList.length) {
      parts.push('.' + Array.from(el.classList).slice(0, 2).join('.'));
    }
    return parts.join('');
  }

  function buildDomPath(el) {
    var chain = [];
    var cur = el;
    var depth = 0;
    while (cur && cur !== document.body && cur !== document.documentElement && depth < 6) {
      var seg = formatTagSelector(cur);
      if (cur.parentElement) {
        var sameTag = Array.from(cur.parentElement.children).filter(function (c) {
          return c.tagName === cur.tagName;
        });
        if (sameTag.length > 1) {
          seg += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')';
        }
      }
      chain.unshift(seg);
      cur = cur.parentElement;
      depth++;
    }
    return chain.join(' > ');
  }

  function inspectTargetAt(xNorm, yNorm) {
    try {
      var vw = window.innerWidth || 1280;
      var vh = window.innerHeight || 720;
      var x = Math.max(0, Math.min(vw - 1, xNorm * vw));
      var y = Math.max(0, Math.min(vh - 1, yNorm * vh));
      var rawHits = document.elementsFromPoint ? document.elementsFromPoint(x, y) : [];
      if (!rawHits.length) {
        var single = document.elementFromPoint(x, y);
        if (single) rawHits = [single];
      }

      var meaningfulHits = rawHits.filter(function (el) {
        if (!el || !el.tagName) return false;
        var tag = el.tagName.toLowerCase();
        if (tag === 'html' || tag === 'body') return false;
        var cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();
        if (cls.indexOf('overlay') !== -1 && tag === 'rect') return false;
        return true;
      });

      var primary = meaningfulHits[0] || rawHits[0] || null;
      var hitStack = meaningfulHits.slice(0, 4).map(function (el) {
        var attrs = {};
        ['fill', 'stroke', 'x', 'y', 'cx', 'cy', 'r', 'width', 'height', 'aria-label', 'data-id', 'data-label', 'data-value'].forEach(function (a) {
          var v = el.getAttribute && el.getAttribute(a);
          if (v !== null && v !== undefined && v !== '') {
            attrs[a] = String(v).slice(0, 48);
          }
        });
        var txt = (el.tagName.toLowerCase() !== 'svg' ? (el.textContent || '') : '')
          .trim()
          .replace(/\\s+/g, ' ')
          .slice(0, 64);
        var r = el.getBoundingClientRect();
        return {
          selector: formatTagSelector(el),
          text: txt || null,
          attrs: attrs,
          bbox: {
            left: Math.round(r.left),
            top: Math.round(r.top),
            width: Math.round(r.width),
            height: Math.round(r.height)
          }
        };
      });

      // Collect closest visible labels / SVG ticks / headings / tooltips near click point
      var labelNodes = document.querySelectorAll('svg text, h1, h2, h3, h4, label, th, td, legend, [class*="title" i], [class*="label" i], [class*="legend" i], [class*="tooltip" i]');
      var nearby = [];
      labelNodes.forEach(function (ln) {
        var t = (ln.textContent || '').trim().replace(/\\s+/g, ' ');
        if (!t || t.length > 90) return;
        var br = ln.getBoundingClientRect();
        if (br.width <= 0 && br.height <= 0) return;
        var cx = br.left + br.width / 2;
        var cy = br.top + br.height / 2;
        var dist = Math.hypot(cx - x, cy - y);
        if (dist <= 130) {
          nearby.push({ text: t, distPx: Math.round(dist) });
        }
      });
      nearby.sort(function (a, b) { return a.distPx - b.distPx; });
      var seenTxt = {};
      var nearbyLabels = [];
      for (var i = 0; i < nearby.length && nearbyLabels.length < 6; i++) {
        if (!seenTxt[nearby[i].text]) {
          seenTxt[nearby[i].text] = true;
          nearbyLabels.push(nearby[i].text);
        }
      }

      // Inspect ECharts / Chart.js canvas metadata if user clicked on a <canvas>
      var canvasContext = null;
      var canvasEl = meaningfulHits.find(function (el) { return el.tagName && el.tagName.toLowerCase() === 'canvas'; });
      if (canvasEl) {
        var cr = canvasEl.getBoundingClientRect();
        var relX = cr.width > 0 ? Math.round(((x - cr.left) / cr.width) * 100) : 50;
        var relY = cr.height > 0 ? Math.round(((y - cr.top) / cr.height) * 100) : 50;
        canvasContext = {
          canvas_pos_pct: { x_pct: relX, y_pct: relY }
        };
        if (window.echarts && typeof window.echarts.getInstanceByDom === 'function') {
          var inst = window.echarts.getInstanceByDom(canvasEl.parentElement) || window.echarts.getInstanceByDom(canvasEl);
          if (inst && typeof inst.getOption === 'function') {
            var opt = inst.getOption() || {};
            var sNames = Array.isArray(opt.series)
              ? opt.series.map(function (s) { return (s.name || s.type || 'series'); }).slice(0, 6)
              : [];
            canvasContext.echarts_series = sNames;
          }
        }
      }

      var domPath = primary ? buildDomPath(primary) : null;
      var summaryParts = [];
      if (domPath) summaryParts.push(domPath);
      if (hitStack.length && hitStack[0].text) summaryParts.push('"' + hitStack[0].text + '"');
      if (nearbyLabels.length) summaryParts.push('nearby=[' + nearbyLabels.slice(0, 4).join(' | ') + ']');
      if (canvasContext && canvasContext.canvas_pos_pct) {
        summaryParts.push('canvas(' + canvasContext.canvas_pos_pct.x_pct + '%,' + canvasContext.canvas_pos_pct.y_pct + '%)');
      }

      return {
        hint: summaryParts.join(' · ').slice(0, 240) || null,
        inspector: {
          click_viewport_px: { x: Math.round(x), y: Math.round(y) },
          click_document_px: { x: Math.round(x + (window.scrollX || 0)), y: Math.round(y + (window.scrollY || 0)) },
          dom_path: domPath,
          hit_stack: hitStack,
          nearby_labels: nearbyLabels,
          canvas_context: canvasContext
        }
      };
    } catch (e) {
      return { hint: null, inspector: null };
    }
  }

  function postScrollState() {
    try {
      var docEl = document.documentElement || document.body;
      window.parent.postMessage({
        type: 'RC_SCROLL_STATE',
        scroll_x: window.scrollX || 0,
        scroll_y: window.scrollY || 0,
        viewport_width: window.innerWidth || 1280,
        viewport_height: window.innerHeight || 720,
        doc_width: (docEl && docEl.scrollWidth) || window.innerWidth || 1280,
        doc_height: (docEl && docEl.scrollHeight) || window.innerHeight || 720
      }, '*');
    } catch (e) {}
  }

  window.addEventListener('scroll', postScrollState, { passive: true });
  window.addEventListener('resize', postScrollState, { passive: true });
  window.addEventListener('load', postScrollState);
  setTimeout(postScrollState, 60);

  window.addEventListener('message', function (event) {
    var msg = event && event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'RC_QUERY_SCROLL') {
      postScrollState();
    } else if (msg.type === 'RC_REQUEST_VIEW_STATE') {
      var targetInfo = (typeof msg.x_norm === 'number' && typeof msg.y_norm === 'number')
        ? inspectTargetAt(msg.x_norm, msg.y_norm)
        : { hint: null, inspector: null };
      var fs = collectFilterState();
      if (targetInfo.inspector) {
        fs.__target_inspector = targetInfo.inspector;
      }
      var payload = {
        type: 'RC_VIEW_STATE',
        request_id: msg.request_id || null,
        element_hint: targetInfo.hint,
        preview_view_state: {
          viewport_width: window.innerWidth || 1280,
          viewport_height: window.innerHeight || 720,
          scroll_x: window.scrollX || 0,
          scroll_y: window.scrollY || 0,
          zoom: 1,
          filter_state: fs
        }
      };
      try {
        window.parent.postMessage(payload, '*');
      } catch (e) {}
    } else if (msg.type === 'RC_APPLY_VIEW_STATE') {
      applyFilterState(msg.filter_state);
      if (typeof msg.scroll_x === 'number' || typeof msg.scroll_y === 'number') {
        window.scrollTo(msg.scroll_x || 0, msg.scroll_y || 0);
      }
    }
  });
})();
</script>
"""
