// Fragment is never sent to the server. No analytics, external assets or auto-submit.
(() => {
  const token = window.location.hash.slice(1);
  if (token) {
    history.replaceState(null, "", window.location.pathname);
    if (/^[A-Za-z0-9_-]{43}$/.test(token)) {
      document.getElementById("invite-token").value = token;
    }
  }
})();
