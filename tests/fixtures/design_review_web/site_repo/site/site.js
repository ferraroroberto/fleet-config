const root = document.documentElement;
const toggle = document.querySelector('.menu-toggle');
toggle?.addEventListener('click', () => {
  const open = !root.classList.contains('menu-open');
  root.classList.toggle('menu-open', open);
  toggle.setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { root.classList.remove('menu-open'); toggle?.setAttribute('aria-expanded', 'false'); }
});
document.querySelectorAll('[data-carousel]').forEach((track) => {
  const nav = track.nextElementSibling;
  const [prev, next] = nav.querySelectorAll('button');
  const step = () => track.firstElementChild.getBoundingClientRect().width + (parseFloat(getComputedStyle(track).columnGap) || 0);
  const update = () => { nav.hidden = track.scrollWidth - track.clientWidth - 2 <= 0; };
  prev.addEventListener('click', () => track.scrollBy({ left: -step(), behavior: 'auto' }));
  next.addEventListener('click', () => track.scrollBy({ left: step(), behavior: 'auto' }));
  window.addEventListener('resize', update);
  update();
});
