(() => {
  const root = document.documentElement;
  const configured = root.dataset.theme;
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  function apply() { root.dataset.theme = configured === 'system' ? (preference.matches ? 'dark' : 'light') : configured; }
  apply(); preference.addEventListener('change', apply);
  const field = document.querySelector('[name="theme"]');
  if (field) field.addEventListener('change', () => { root.dataset.theme = field.value === 'system' ? (preference.matches ? 'dark' : 'light') : field.value; });
})();
