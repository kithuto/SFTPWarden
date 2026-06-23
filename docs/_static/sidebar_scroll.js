(() => {
  const nativeScrollIntoView = Element.prototype.scrollIntoView;

  Element.prototype.scrollIntoView = function scrollIntoViewWithoutSidebarJump(...args) {
    if (this.closest && this.closest(".wy-menu-vertical")) {
      return;
    }
    return nativeScrollIntoView.apply(this, args);
  };

  function keepSidebarPositionDuringNavigation() {
    const sidebar = document.querySelector(".wy-side-scroll");
    const menu = document.querySelector(".wy-menu-vertical");
    if (!sidebar || !menu) {
      return;
    }

    let savedScrollTop = sidebar.scrollTop;

    sidebar.addEventListener(
      "scroll",
      () => {
        savedScrollTop = sidebar.scrollTop;
      },
      { passive: true },
    );

    menu.addEventListener(
      "click",
      () => {
        savedScrollTop = sidebar.scrollTop;
        requestAnimationFrame(() => {
          sidebar.scrollTop = savedScrollTop;
        });
        setTimeout(() => {
          sidebar.scrollTop = savedScrollTop;
        }, 0);
      },
      true,
    );
  }

  document.addEventListener("DOMContentLoaded", keepSidebarPositionDuringNavigation);
})();
