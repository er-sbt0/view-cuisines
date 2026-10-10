// Tiny progressive enhancements: ingredient rows and print button.
document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add-row]");
  if (add) {
    const list = document.getElementById("ingredients");
    const template = document.getElementById("ingredient-row-template");
    list.appendChild(template.content.cloneNode(true));
    list.lastElementChild.querySelector("input").focus();
    return;
  }
  const remove = event.target.closest("[data-remove-row]");
  if (remove) {
    const row = remove.closest(".ingredient-row");
    if (row.parentElement.children.length > 1) {
      row.remove();
    } else {
      row.querySelectorAll("input").forEach((input) => (input.value = ""));
    }
    return;
  }
  if (event.target.closest("[data-print]")) {
    window.print();
  }
});
