/**
 * Landing page: nav scroll state + reveal-on-scroll.
 */
(function () {
  const nav = document.getElementById("lpNav");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function syncNav() {
    if (!nav) return;
    nav.classList.toggle("is-scrolled", window.scrollY > 8);
  }

  syncNav();
  window.addEventListener("scroll", syncNav, { passive: true });

  const nodes = document.querySelectorAll(".lp-reveal");
  if (!nodes.length) return;

  if (reduceMotion || !("IntersectionObserver" in window)) {
    nodes.forEach(function (el) {
      el.classList.add("is-in");
    });
    return;
  }

  const io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        io.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
  );

  nodes.forEach(function (el) {
    io.observe(el);
  });
})();
