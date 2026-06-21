(() => {
  const MAX_RESULTS = 10;
  const MIN_QUERY_LENGTH = 2;
  const SNIPPET_RADIUS = 95;

  const initialHighlightQuery = new URL(window.location.href).searchParams.get("highlight") || "";

  const state = {
    activeIndex: -1,
    index: null,
    indexPromise: null,
    links: [],
    modalReady: false,
    pageCache: new Map(),
    searchGeneration: 0,
    sourceCache: new Map(),
  };

  function createElement(tag, className, textContent) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (textContent) {
      element.textContent = textContent;
    }
    return element;
  }

  function scriptUrl(path) {
    const script = document.currentScript || document.querySelector('script[src*="search_modal.js"]');
    const current = script ? new URL(script.src, window.location.href) : new URL("_static/search_modal.js", window.location.href);
    return new URL(path, current);
  }

  function searchIndexUrl() {
    return scriptUrl("../searchindex.js");
  }

  function loadSearchIndex() {
    if (state.index) {
      return Promise.resolve(state.index);
    }
    if (state.indexPromise) {
      return state.indexPromise;
    }

    state.indexPromise = new Promise((resolve, reject) => {
      const previousSearch = window.Search;
      const script = document.createElement("script");

      window.Search = {
        setIndex(index) {
          state.index = index;
          if (previousSearch) {
            window.Search = previousSearch;
          }
          resolve(index);
        },
      };

      script.src = searchIndexUrl().href;
      script.async = true;
      script.onerror = () => {
        if (previousSearch) {
          window.Search = previousSearch;
        }
        reject(new Error("Unable to load search index."));
      };
      document.head.append(script);
    });

    return state.indexPromise;
  }

  function ensureModal() {
    if (state.modalReady) {
      return;
    }

    const overlay = createElement("div", "doc-search-overlay");
    overlay.id = "doc-search-overlay";
    overlay.hidden = true;

    const modal = createElement("div", "doc-search-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "doc-search-title");

    const header = createElement("div", "doc-search-header");
    const title = createElement("h2", "doc-search-title", "Search documentation");
    title.id = "doc-search-title";
    const close = createElement("button", "doc-search-close", "x");
    close.type = "button";
    close.setAttribute("aria-label", "Close search");
    header.append(title, close);

    const searchBox = createElement("div", "doc-search-box");
    const input = createElement("input", "doc-search-input");
    input.id = "doc-search-input";
    input.type = "search";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Search SFTPWarden docs";
    input.setAttribute("aria-label", "Search SFTPWarden documentation");
    const shortcut = createElement("span", "doc-search-shortcut", shortcutText());
    searchBox.append(input, shortcut);

    const status = createElement("div", "doc-search-status", "Search commands, providers, deploy, watcher, or security.");
    status.id = "doc-search-status";

    const results = createElement("div", "doc-search-results");
    results.id = "doc-search-results";

    modal.append(header, searchBox, status, results);
    overlay.append(modal);
    document.body.append(overlay);

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeModal();
      }
    });
    close.addEventListener("click", closeModal);
    input.addEventListener("input", () => search(input.value));
    input.addEventListener("keydown", handleModalKeyboard);

    state.modalReady = true;
  }

  function modalElements() {
    return {
      input: document.getElementById("doc-search-input"),
      overlay: document.getElementById("doc-search-overlay"),
      results: document.getElementById("doc-search-results"),
      status: document.getElementById("doc-search-status"),
    };
  }

  function openModal(initialQuery = "") {
    ensureModal();
    const { input, overlay } = modalElements();
    overlay.hidden = false;
    document.documentElement.classList.add("doc-search-open");

    if (initialQuery) {
      input.value = initialQuery;
      search(initialQuery);
    } else if (!input.value) {
      clearResults();
      setStatus("Search commands, providers, deploy, watcher, or security.");
    }

    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  }

  function closeModal() {
    const { overlay } = modalElements();
    if (!overlay) {
      return;
    }
    overlay.hidden = true;
    document.documentElement.classList.remove("doc-search-open");
    state.activeIndex = -1;
  }

  function setStatus(message) {
    const { status } = modalElements();
    status.textContent = message;
  }

  function clearResults() {
    const { results } = modalElements();
    results.replaceChildren();
    state.links = [];
    state.activeIndex = -1;
  }

  function queryTokens(query) {
    return Array.from(
      new Set(
        query
          .toLowerCase()
          .split(/[^a-z0-9_]+/)
          .map((token) => token.trim())
          .filter((token) => token.length >= MIN_QUERY_LENGTH),
      ),
    );
  }

  function escapedRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function snippetTerms(query) {
    const exactQuery = query.trim().toLowerCase();
    return Array.from(new Set([exactQuery, ...queryTokens(query)]))
      .filter((term) => term.length >= MIN_QUERY_LENGTH)
      .sort((left, right) => right.length - left.length);
  }

  function normalizeIds(value) {
    if (value === undefined || value === null) {
      return [];
    }
    if (Array.isArray(value)) {
      return value.flatMap(normalizeIds);
    }
    return [Number(value)];
  }

  function cleanText(text) {
    return text.replace(/[\u00b6\uf0c1]/g, "").replace(/\s+/g, " ").trim();
  }

  function docUrl(index, docId) {
    const docname = index.docnames[docId];
    return new URL(`${docname}.html`, new URL("search.html", window.location.href));
  }

  function sourceUrl(index, docId) {
    const filename = index.filenames && index.filenames[docId];
    if (!filename) {
      return null;
    }
    return new URL(`_sources/${filename}.txt`, new URL("search.html", window.location.href));
  }

  function resultUrl(index, result, terms) {
    const url = docUrl(index, result.docId);
    if (terms.length) {
      url.searchParams.set("highlight", terms.join(" "));
    }
    if (result.anchor) {
      url.hash = result.anchor;
    }
    return url;
  }

  function emptyResult(docId, index) {
    return {
      anchor: "",
      docId,
      score: 0,
      sectionTitle: "",
      snippet: "",
      title: index.titles[docId] || index.docnames[docId],
    };
  }

  function addResult(results, docId, index, score, section) {
    if (!Number.isInteger(docId) || docId < 0 || docId >= index.docnames.length) {
      return;
    }
    if (!results.has(docId)) {
      results.set(docId, emptyResult(docId, index));
    }
    const result = results.get(docId);
    result.score += score;
    if (section && !result.sectionTitle) {
      result.sectionTitle = section;
      result.anchor = anchorFromIndex(index, result.docId, section);
      result.snippet = `Section: ${section}`;
    }
  }

  function anchorFromIndex(index, docId, sectionTitle) {
    if (!sectionTitle || !index.alltitles || !index.alltitles[sectionTitle]) {
      return "";
    }
    const entry = index.alltitles[sectionTitle].find(([entryDocId]) => entryDocId === docId);
    return entry && entry[1] ? entry[1] : "";
  }

  function searchIndex(index, query) {
    const tokens = queryTokens(query);
    const normalized = query.trim().toLowerCase();
    const results = new Map();

    if (!tokens.length) {
      return [];
    }

    index.titles.forEach((title, docId) => {
      const haystack = `${title} ${index.docnames[docId]}`.toLowerCase();
      if (tokens.every((token) => haystack.includes(token))) {
        addResult(results, docId, index, title.toLowerCase().includes(normalized) ? 30 : 18);
      }
    });

    Object.entries(index.alltitles || {}).forEach(([title, entries]) => {
      const titleText = title.toLowerCase();
      if (!tokens.some((token) => titleText.includes(token))) {
        return;
      }
      entries.forEach(([docId]) => addResult(results, docId, index, 14, title));
    });

    for (const token of tokens) {
      Object.entries(index.titleterms || {}).forEach(([term, ids]) => {
        if (term.includes(token) || token.includes(term)) {
          normalizeIds(ids).forEach((docId) => addResult(results, docId, index, 10));
        }
      });
      Object.entries(index.terms || {}).forEach(([term, ids]) => {
        if (term.includes(token) || token.includes(term)) {
          normalizeIds(ids).forEach((docId) => addResult(results, docId, index, 4));
        }
      });
    }

    return Array.from(results.values()).sort(
      (left, right) => right.score - left.score || left.title.localeCompare(right.title),
    );
  }

  function fetchDocument(index, docId) {
    const docname = index.docnames[docId];
    if (state.pageCache.has(docname)) {
      return state.pageCache.get(docname);
    }

    const promise = fetch(docUrl(index, docId).href, { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to load ${docname}.html`);
        }
        return response.text();
      })
      .then((html) => new DOMParser().parseFromString(html, "text/html"))
      .catch(() => null);

    state.pageCache.set(docname, promise);
    return promise;
  }

  function fetchSource(index, docId) {
    const filename = index.filenames && index.filenames[docId];
    const url = sourceUrl(index, docId);
    if (!filename || !url) {
      return Promise.resolve(null);
    }
    if (state.sourceCache.has(filename)) {
      return state.sourceCache.get(filename);
    }

    const promise = fetch(url.href, { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to load ${filename}.txt`);
        }
        return response.text();
      })
      .catch(() => null);

    state.sourceCache.set(filename, promise);
    return promise;
  }

  function contentRoot(doc) {
    return doc.querySelector(".rst-content .document")
      || doc.querySelector(".rst-content")
      || doc.body;
  }

  function firstMatchInText(text, terms, exactQuery) {
    const lowerText = text.toLowerCase();
    if (exactQuery.length >= MIN_QUERY_LENGTH) {
      const exactIndex = lowerText.indexOf(exactQuery);
      if (exactIndex !== -1) {
        return {
          exact: true,
          index: exactIndex,
          length: exactQuery.length,
        };
      }
    }

    let match = null;
    terms.forEach((term) => {
      const index = lowerText.indexOf(term);
      if (index !== -1 && (!match || index < match.index)) {
        match = {
          exact: false,
          index,
          length: term.length,
        };
      }
    });
    return match;
  }

  function sectionForNode(node, root) {
    const element = node.parentElement;
    return (element && element.closest("section[id], .section[id]"))
      || (root.matches && root.matches("section[id], .section[id]") ? root : null)
      || root.querySelector("section[id], .section[id]")
      || root;
  }

  function sectionHeading(section) {
    if (!section) {
      return "";
    }
    const heading = Array.from(section.children).find((child) => /^H[1-6]$/.test(child.tagName));
    return heading ? cleanText(heading.textContent) : "";
  }

  function sectionScore(section, terms, exactQuery, pageTitle) {
    const title = sectionHeading(section).toLowerCase();
    const text = cleanText(section.textContent || "").toLowerCase();
    let score = 0;

    if (exactQuery && text.includes(exactQuery)) {
      score += 45;
    }
    if (exactQuery && title.includes(exactQuery)) {
      score += 35;
    }
    if (pageTitle.toLowerCase().includes(exactQuery)) {
      score += 20;
    }
    terms.forEach((term) => {
      if (title.includes(term)) {
        score += 20;
      }
      if (text.includes(term)) {
        score += 9;
      }
    });
    if (terms.every((term) => text.includes(term))) {
      score += 25;
    }
    return score;
  }

  function textNodes(root) {
    const nodes = [];
    const ownerDocument = root.ownerDocument || document;
    const walker = ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue || !node.nodeValue.trim()) {
          return NodeFilter.FILTER_REJECT;
        }
        const parent = node.parentElement;
        if (!parent || parent.closest("script, style, noscript, .headerlink, .doc-search-overlay")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    while (walker.nextNode()) {
      nodes.push(walker.currentNode);
    }
    return nodes;
  }

  function snippet(text, terms, exactQuery) {
    const cleaned = cleanText(text);
    const match = firstMatchInText(cleaned, terms, exactQuery);
    if (!match) {
      return cleaned.slice(0, SNIPPET_RADIUS * 2);
    }

    const start = Math.max(0, match.index - SNIPPET_RADIUS);
    const end = Math.min(cleaned.length, match.index + match.length + SNIPPET_RADIUS);
    const prefix = start > 0 ? "... " : "";
    const suffix = end < cleaned.length ? " ..." : "";
    return `${prefix}${cleaned.slice(start, end)}${suffix}`;
  }

  function appendHighlightedSnippet(container, text, query) {
    const terms = snippetTerms(query);
    if (!terms.length) {
      container.textContent = text;
      return;
    }

    const expression = new RegExp(`(${terms.map(escapedRegExp).join("|")})`, "gi");
    let cursor = 0;
    let match = expression.exec(text);

    while (match) {
      if (match.index > cursor) {
        container.append(document.createTextNode(text.slice(cursor, match.index)));
      }

      const marker = createElement("mark", "doc-search-result__match", match[0]);
      container.append(marker);
      cursor = match.index + match[0].length;
      match = expression.exec(text);
    }

    if (cursor < text.length) {
      container.append(document.createTextNode(text.slice(cursor)));
    }
  }

  function pageTitle(doc, index, docId) {
    return cleanText(doc.querySelector("h1")?.textContent || index.titles[docId] || index.docnames[docId]);
  }

  function markdownHeading(text) {
    return cleanText(text.replace(/^#+\s*/, "").replace(/\s*\{#[^}]+}\s*$/, ""));
  }

  function sourceSectionForOffset(source, offset) {
    const beforeMatch = source.slice(0, offset);
    const headings = Array.from(beforeMatch.matchAll(/^#{1,6}\s+(.+)$/gm));
    if (!headings.length) {
      return "";
    }
    return markdownHeading(headings[headings.length - 1][1]);
  }

  function findSourceResult(source, index, docId, query) {
    if (!source) {
      return null;
    }

    const terms = queryTokens(query);
    const exactQuery = query.trim().toLowerCase();
    const cleaned = cleanText(source);
    const lowerSource = cleaned.toLowerCase();
    if (!terms.length || !terms.every((term) => lowerSource.includes(term))) {
      return null;
    }

    const match = firstMatchInText(source, terms, exactQuery);
    const sectionTitle = sourceSectionForOffset(source, match ? match.index : 0);

    return {
      anchor: anchorFromIndex(index, docId, sectionTitle),
      docId,
      score: (match && match.exact ? 50 : 20) + (sectionTitle ? 10 : 0),
      sectionTitle,
      snippet: snippet(cleaned, terms, exactQuery),
      title: index.titles[docId] || index.docnames[docId],
    };
  }

  function findDocumentResult(doc, index, docId, query) {
    if (!doc) {
      return null;
    }

    const terms = queryTokens(query);
    const exactQuery = query.trim().toLowerCase();
    const root = contentRoot(doc);
    const title = pageTitle(doc, index, docId);
    const pageText = cleanText(`${title} ${root.textContent || ""}`).toLowerCase();

    if (!terms.length || !terms.every((term) => pageText.includes(term))) {
      return null;
    }

    let best = null;
    textNodes(root).forEach((node) => {
      const match = firstMatchInText(node.nodeValue, terms, exactQuery);
      if (!match) {
        return;
      }
      const section = sectionForNode(node, root);
      const score = sectionScore(section, terms, exactQuery, title) + (match.exact ? 30 : 0);
      if (!best || score > best.score) {
        best = {
          match,
          score,
          section,
          text: section.textContent || node.nodeValue,
        };
      }
    });

    if (!best) {
      const section = Array.from(root.querySelectorAll("section[id], .section[id]")).find((candidate) => {
        const text = cleanText(candidate.textContent || "").toLowerCase();
        return terms.every((term) => text.includes(term));
      }) || root;
      best = {
        match: firstMatchInText(section.textContent || "", terms, exactQuery),
        score: sectionScore(section, terms, exactQuery, title),
        section,
        text: section.textContent || root.textContent || "",
      };
    }

    return {
      anchor: best.section.id || "",
      docId,
      score: best.score,
      sectionTitle: sectionHeading(best.section),
      snippet: snippet(best.text, terms, exactQuery),
      title,
    };
  }

  async function searchDocuments(index, query) {
    const indexedResults = searchIndex(index, query);
    const indexedDocIds = indexedResults.map((result) => result.docId);
    const docIds = Array.from(new Set([...indexedDocIds, ...index.docnames.map((_, docId) => docId)]));

    const documentResults = await Promise.all(
      docIds.map((docId) => fetchDocument(index, docId).then((doc) => findDocumentResult(doc, index, docId, query))),
    );
    let realResults = documentResults
      .filter(Boolean)
      .sort((left, right) => right.score - left.score || left.title.localeCompare(right.title))
      .slice(0, MAX_RESULTS);

    if (realResults.length) {
      return realResults;
    }

    const sourceResults = await Promise.all(
      docIds.map((docId) => fetchSource(index, docId).then((source) => findSourceResult(source, index, docId, query))),
    );
    realResults = sourceResults
      .filter(Boolean)
      .sort((left, right) => right.score - left.score || left.title.localeCompare(right.title))
      .slice(0, MAX_RESULTS);

    return realResults.length ? realResults : indexedResults.slice(0, MAX_RESULTS);
  }

  function renderResults(index, query, results) {
    const { results: container } = modalElements();
    const terms = queryTokens(query);
    container.replaceChildren();
    state.links = [];
    state.activeIndex = -1;

    if (!results.length) {
      setStatus(`No results found for "${query}".`);
      return;
    }

    setStatus(`${results.length} result${results.length === 1 ? "" : "s"}`);

    results.forEach((result) => {
      const item = createElement("div", "doc-search-result");
      const link = createElement("a", "doc-search-result__link");
      const url = resultUrl(index, result, terms);
      const titleText = result.sectionTitle && result.sectionTitle !== result.title
        ? `${result.title}: ${result.sectionTitle}`
        : result.title;
      const title = createElement("span", "doc-search-result__title", titleText);
      const path = createElement("span", "doc-search-result__path", url.pathname.replace(/\/index\.html$/, "/"));
      const resultSnippet = createElement("span", "doc-search-result__snippet");
      const snippetText = result.snippet
        || (result.sectionTitle ? `Section: ${result.sectionTitle}` : "Open this page to view highlighted matches.");

      link.href = url.href;
      appendHighlightedSnippet(resultSnippet, snippetText, query);
      link.append(title, path, resultSnippet);
      item.append(link);
      container.append(item);
      state.links.push(link);
    });
  }

  function search(query) {
    const trimmed = query.trim();
    state.searchGeneration += 1;
    const generation = state.searchGeneration;

    if (trimmed.length < MIN_QUERY_LENGTH) {
      clearResults();
      setStatus("Type at least 2 characters.");
      return;
    }

    setStatus("Searching...");
    loadSearchIndex()
      .then((index) => searchDocuments(index, trimmed).then((results) => ({ index, results })))
      .then(({ index, results }) => {
        if (generation === state.searchGeneration) {
          renderResults(index, trimmed, results);
        }
      })
      .catch(() => setStatus("The search index could not be loaded."));
  }

  function setActiveResult(index) {
    state.links.forEach((link) => link.classList.remove("is-active"));
    state.activeIndex = index;
    if (state.links[state.activeIndex]) {
      state.links[state.activeIndex].classList.add("is-active");
      state.links[state.activeIndex].scrollIntoView({ block: "nearest" });
    }
  }

  function handleModalKeyboard(event) {
    if (event.key === "Escape") {
      closeModal();
      return;
    }
    if (event.key === "ArrowDown" && state.links.length) {
      event.preventDefault();
      setActiveResult((state.activeIndex + 1) % state.links.length);
      return;
    }
    if (event.key === "ArrowUp" && state.links.length) {
      event.preventDefault();
      setActiveResult((state.activeIndex - 1 + state.links.length) % state.links.length);
      return;
    }
    if (event.key === "Enter" && state.activeIndex >= 0 && state.links[state.activeIndex]) {
      event.preventDefault();
      state.links[state.activeIndex].click();
    }
  }

  function shortcutText() {
    return /Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd K" : "Ctrl K";
  }

  function installSearchTrigger() {
    const form = document.querySelector(".wy-side-nav-search form");
    if (!form) {
      return;
    }

    const button = createElement("button", "doc-search-button");
    button.type = "button";
    button.setAttribute("aria-label", "Search documentation");
    button.append(createElement("span", "", "Search docs"));
    button.append(createElement("kbd", "", shortcutText()));

    form.replaceChildren(button);
    form.addEventListener("submit", (event) => event.preventDefault());
    button.addEventListener("click", () => openModal());
  }

  function highlightedTarget() {
    const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    const section = hash ? document.getElementById(hash) : null;
    return (section && section.querySelector(".highlighted"))
      || document.querySelector(".rst-content .highlighted");
  }

  function scrollToHighlightedTerm() {
    if (!initialHighlightQuery.trim()) {
      return;
    }

    let attempts = 0;
    const scrollWhenReady = () => {
      const target = highlightedTarget();
      if (target) {
        target.scrollIntoView({ block: "center", inline: "nearest" });
        return;
      }
      attempts += 1;
      if (attempts < 30) {
        window.setTimeout(scrollWhenReady, 50);
      }
    };

    window.setTimeout(scrollWhenReady, 25);
  }

  document.addEventListener("keydown", (event) => {
    const isShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
    if (isShortcut) {
      event.preventDefault();
      openModal();
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    installSearchTrigger();
    scrollToHighlightedTerm();
  });
})();
