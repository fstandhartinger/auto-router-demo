/* Rendering a model's answer: markdown, maths, highlighted code.
 *
 * Three rules this file exists to keep:
 *
 * 1. **Nothing a model writes becomes markup on trust.** Markdown is parsed,
 *    then the result goes through DOMPurify before it touches the document.
 * 2. **Maths is taken out before markdown runs and put back after.** `$x_1$`
 *    through a markdown parser comes out with an italic `1`; and a page about
 *    dollars per million tokens must not turn "$5 a day" into an equation, so
 *    the single-dollar form is deliberately fussy about what it accepts.
 * 3. **The libraries are optional.** They come from a CDN with subresource
 *    integrity; if any of that fails the answer still renders, as escaped text
 *    with fenced code, and the page says nothing about it.
 */

const CDN = "https://cdn.jsdelivr.net/npm";

const SCRIPTS = [
  { global: "marked", url: `${CDN}/marked@18.0.13/lib/marked.umd.js`,
    sri: "sha384-Jy8qDMspJASzATgFngF2ompIKy0StbCcvuTE65mxDm/E0/YSIF6Ndc+5V7bbwRcw" },
  { global: "DOMPurify", url: `${CDN}/dompurify@3.4.15/dist/purify.min.js`,
    sri: "sha384-uUMu9JDY09vBzRf9SPcK2VgUj+W/70J6Soc+Dded5P474ElQ63iv9j5N3DE7Kp3N" },
  { global: "katex", url: `${CDN}/katex@0.18.7/dist/katex.min.js`,
    sri: "sha384-+7Keh381hSkXmXqnjC0JBM/kzsN6TFj+wMKychSLjTvJ8/0ElMde2uKl8i6p6Buj" },
  { global: "hljs", url: `${CDN}/@highlightjs/cdn-assets@11.12.0/highlight.min.js`,
    sri: "sha384-wjfDDhOPPdjtva8vWBhWeVprSpmxisEu5aYT3q1JyACqXpdKpo3PWZTMVq24MBix" },
];

const KATEX_CSS = {
  url: `${CDN}/katex@0.18.7/dist/katex.min.css`,
  sri: "sha384-JctiRyLzXCrSoOOzFlSoWLdyzQl7OrrRnhyeBmzB6ZWtcjccUyc8lCQJqIbs3uQX",
};

let loading = null;
let libs = null;

function loadScript(spec) {
  return new Promise((resolve) => {
    if (window[spec.global]) return resolve(window[spec.global]);
    const tag = document.createElement("script");
    tag.src = spec.url;
    tag.integrity = spec.sri;
    tag.crossOrigin = "anonymous";
    tag.referrerPolicy = "no-referrer";
    tag.addEventListener("load", () => resolve(window[spec.global] || null));
    tag.addEventListener("error", () => resolve(null));
    document.head.appendChild(tag);
  });
}

function loadCss(spec) {
  if (document.querySelector(`link[href="${spec.url}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = spec.url;
  link.integrity = spec.sri;
  link.crossOrigin = "anonymous";
  link.referrerPolicy = "no-referrer";
  document.head.appendChild(link);
}

/** Fetch the renderers once, in the background. Safe to call as often as you like. */
export function warmRenderers() {
  if (loading) return loading;
  loadCss(KATEX_CSS);
  loading = Promise.all(SCRIPTS.map(loadScript)).then(([marked, purify, katex, hljs]) => {
    if (marked && marked.marked) marked = marked.marked;
    if (marked) {
      marked.setOptions({ gfm: true, breaks: false, headerIds: false, mangle: false });
    }
    libs = { marked, purify, katex, hljs };
    return libs;
  });
  return loading;
}

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------- fallback */
/** Escaped text with fenced code, inline code and bold. Used until the
 *  libraries are there, and for good if they never arrive. */
export function renderPlain(text) {
  let html = escapeHtml(text);
  html = html.replace(/```([a-z]*)\n([\s\S]*?)(?:```|$)/g,
    (_m, _lang, code) => `<pre><code>${code}</code></pre>`);
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  return html.replace(/\n{2,}/g, "<br><br>");
}

/* ----------------------------------------------------------------- maths */
const DISPLAY = [["$$", "$$"], ["\\[", "\\]"]];
const INLINE = [["\\(", "\\)"]];

/** Is this `$…$` run plausibly maths rather than two prices in one sentence? */
function looksLikeMath(body) {
  if (!body || /^\s|\s$/.test(body) || body.includes("\n")) return false;
  if (body.length > 400) return false;
  // "$5 a day … $4" — a run that starts with a figure and then has words in it
  // is money, not an equation. `$5$` on its own still renders.
  if (/^[\d.,]/.test(body) && /\s/.test(body)) return false;
  return true;
}

/** Pull every maths run out of the text, leaving a placeholder behind. */
function extractMath(text) {
  const found = [];
  let out = "";
  let i = 0;
  // A private-use pair, not NUL: CommonMark requires a parser to replace U+0000
  // with the replacement character, which would eat the placeholder.
  const token = () => `\uE000MATH${found.length - 1}\uE001`;

  while (i < text.length) {
    // Code is never maths: skip fenced blocks and inline spans whole.
    if (text.startsWith("```", i)) {
      const end = text.indexOf("```", i + 3);
      const stop = end === -1 ? text.length : end + 3;
      out += text.slice(i, stop);
      i = stop;
      continue;
    }
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      const stop = end === -1 ? text.length : end + 1;
      out += text.slice(i, stop);
      i = stop;
      continue;
    }
    let matched = false;
    for (const [open, close] of DISPLAY.concat(INLINE)) {
      if (!text.startsWith(open, i)) continue;
      const end = text.indexOf(close, i + open.length);
      if (end === -1) continue;
      const body = text.slice(i + open.length, end);
      found.push({ tex: body, display: DISPLAY.some(([o]) => o === open) });
      out += token();
      i = end + close.length;
      matched = true;
      break;
    }
    if (matched) continue;
    if (text[i] === "$" && text[i + 1] !== "$") {
      const end = text.indexOf("$", i + 1);
      if (end !== -1) {
        const body = text.slice(i + 1, end);
        if (looksLikeMath(body)) {
          found.push({ tex: body, display: false });
          out += token();
          i = end + 1;
          continue;
        }
      }
    }
    out += text[i];
    i += 1;
  }
  return { text: out, found };
}

function putMathBack(html, found, katex) {
  return html.replace(/\uE000MATH(\d+)\uE001/g, (_m, index) => {
    const item = found[Number(index)];
    if (!item) return "";
    if (!katex) return escapeHtml((item.display ? "$$" : "$") + item.tex + (item.display ? "$$" : "$"));
    try {
      return katex.renderToString(item.tex, {
        displayMode: item.display, throwOnError: false, output: "html",
        strict: false, trust: false, maxSize: 30, maxExpand: 300,
      });
    } catch {
      return escapeHtml(item.tex);
    }
  });
}

/* ------------------------------------------------------------- the entry */
/** Markdown + maths + highlighted code as sanitised HTML. */
export function renderRich(text) {
  if (!libs || !libs.marked || !libs.purify) return renderPlain(text);
  const { marked, purify, katex, hljs } = libs;
  const { text: stripped, found } = extractMath(text);
  let html;
  try {
    html = marked.parse(stripped);
  } catch {
    return renderPlain(text);
  }
  html = purify.sanitize(html, {
    ADD_ATTR: ["target", "rel"],
    FORBID_TAGS: ["style", "form", "input", "button", "iframe", "object", "embed"],
    FORBID_ATTR: ["style", "onerror", "onload"],
  });
  html = putMathBack(html, found, katex);

  const holder = document.createElement("div");
  holder.innerHTML = html;
  for (const link of holder.querySelectorAll("a[href]")) {
    link.target = "_blank";
    link.rel = "noopener nofollow ugc";
  }
  if (hljs) {
    for (const block of holder.querySelectorAll("pre code")) {
      const language = [...block.classList]
        .map((c) => (c.startsWith("language-") ? c.slice(9) : null))
        .find(Boolean);
      try {
        const result = language && hljs.getLanguage(language)
          ? hljs.highlight(block.textContent, { language, ignoreIllegals: true })
          : hljs.highlightAuto(block.textContent);
        block.innerHTML = result.value;
        block.classList.add("hljs");
      } catch { /* an unknown dialect is not worth a broken answer */ }
    }
  }
  for (const table of holder.querySelectorAll("table")) {
    const scroll = document.createElement("div");
    scroll.className = "table-scroll";
    table.replaceWith(scroll);
    scroll.appendChild(table);
  }
  return holder.innerHTML;
}

/** True once the libraries are in (so callers can re-render when they land). */
export function renderersReady() {
  return Boolean(libs && libs.marked);
}
