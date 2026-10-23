// Boutons « Copier »
document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    const text = document.querySelector(btn.dataset.copy).textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
      const old = btn.textContent;
      btn.textContent = document.body.dataset.copied || "✓";
      setTimeout(() => (btn.textContent = old), 1500);
    } catch {
      window.prompt("Copiez :", text);
    }
  });
});

// Formulaire paquet : n'affiche que les champs de la source choisie
const source = document.getElementById("source");
if (source) {
  const sync = () =>
    document.querySelectorAll("[data-source]").forEach((el) => {
      el.hidden = !el.dataset.source.split(" ").includes(source.value);
    });
  source.addEventListener("change", sync);
  sync();
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
  });
});

// Dates (timestamps Unix) affichées dans le fuseau du navigateur
document.querySelectorAll("[data-ts]").forEach((el) => {
  const ts = Number(el.dataset.ts);
  if (ts > 0) el.textContent = new Date(ts * 1000).toLocaleString();
});
