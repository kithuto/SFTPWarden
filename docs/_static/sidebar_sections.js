(() => {
  const SKIPPED_HEADINGS = new Set(["Table of Contents"]);

  function currentNavItem() {
    return document.querySelector(".wy-menu-vertical li.current");
  }

  function existingSectionList(item) {
    return item ? item.querySelector(":scope > ul") : null;
  }

  function pageHeadings() {
    return Array.from(document.querySelectorAll(".rst-content section[id] > h2"))
      .map((heading) => {
        const section = heading.closest("section[id]");
        const title = heading.textContent.replace("\uf0c1", "").trim();
        return {
          href: `#${section.id}`,
          title,
        };
      })
      .filter((entry) => entry.title && !SKIPPED_HEADINGS.has(entry.title));
  }

  function buildSectionList(headings) {
    const list = document.createElement("ul");
    headings.forEach((heading) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      item.className = "toctree-l2";
      link.className = "reference internal";
      link.href = heading.href;
      link.textContent = heading.title;
      item.append(link);
      list.append(item);
    });
    return list;
  }

  function installCurrentPageSections() {
    const item = currentNavItem();
    if (!item || existingSectionList(item)) {
      return;
    }

    const headings = pageHeadings();
    if (!headings.length) {
      return;
    }

    item.append(buildSectionList(headings));
  }

  document.addEventListener("DOMContentLoaded", installCurrentPageSections);
})();
