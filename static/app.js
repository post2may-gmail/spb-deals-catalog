(() => {
  const input = document.getElementById("street-input");
  const list = document.getElementById("street-suggest");
  if (input && list) {
    let timer = null;
    input.addEventListener("input", () => {
      const q = input.value.trim();
      window.clearTimeout(timer);
      if (q.length < 2) {
        list.innerHTML = "";
        return;
      }
      timer = window.setTimeout(async () => {
        try {
          const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          list.innerHTML = "";
          for (const item of data.items || []) {
            const opt = document.createElement("option");
            // В input уходит короткое имя улицы; в подсказке видно полное с типом.
            opt.value = item.street;
            const label = item.title || item.street;
            if (label && label !== item.street) {
              opt.label = label;
              opt.textContent = label;
            }
            list.appendChild(opt);
          }
        } catch (_) {
          /* ignore */
        }
      }, 180);
    });
  }

  // Периоды только от раннего к позднему (или «все»).
  document.querySelectorAll("[data-period-guard]").forEach((form) => {
    const from = form.querySelector('[name="period_from"]');
    const to = form.querySelector('[name="period_to"]');
    if (!from || !to) return;

    const sync = () => {
      const fromVal = from.value;
      const toVal = to.value;
      for (const opt of to.options) {
        if (!opt.value) {
          opt.disabled = false;
          continue;
        }
        opt.disabled = Boolean(fromVal) && opt.value < fromVal;
      }
      for (const opt of from.options) {
        if (!opt.value) {
          opt.disabled = false;
          continue;
        }
        opt.disabled = Boolean(toVal) && opt.value > toVal;
      }
      if (toVal && fromVal && toVal < fromVal) {
        to.value = "";
      }
    };

    from.addEventListener("change", sync);
    to.addEventListener("change", sync);
    form.addEventListener("submit", (ev) => {
      if (from.value && to.value && to.value < from.value) {
        ev.preventDefault();
        alert(
          "Период «с» не может быть позже периода «по». Выберите кварталы от раннего к позднему или «Все кварталы»."
        );
      }
    });
    sync();
  });
})();
