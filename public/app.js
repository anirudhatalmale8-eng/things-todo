'use strict';

const els = {
  composer: document.getElementById('composer'),
  input: document.getElementById('new-todo'),
  list: document.getElementById('list'),
  empty: document.getElementById('empty'),
  summary: document.getElementById('summary'),
  clearDone: document.getElementById('clear-done'),
  template: document.getElementById('row-template'),
  filters: document.querySelectorAll('.filters__btn'),
  confirm: document.getElementById('confirm'),
  confirmItem: document.getElementById('confirm-item'),
  confirmCancel: document.querySelector('[data-testid="confirm-cancel"]'),
};

let todos = [];
let filter = 'all';

// -------------------------------------------------------------------- api

async function api(path, options = {}) {
  const res = await fetch(`/api/todos${path}`, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.error || `request failed (${res.status})`);
  }
  return res.json();
}

// ------------------------------------------------------------------ render

function visible() {
  if (filter === 'active') return todos.filter((t) => !t.done);
  if (filter === 'done') return todos.filter((t) => t.done);
  return todos;
}

function render() {
  const rows = visible();

  els.list.replaceChildren(
    ...rows.map((todo) => {
      const row = els.template.content.firstElementChild.cloneNode(true);
      row.dataset.id = todo.id;
      row.classList.toggle('is-done', todo.done);

      const checkbox = row.querySelector('[data-testid="toggle"]');
      checkbox.checked = todo.done;
      checkbox.setAttribute('aria-label', `Mark "${todo.title}" as done`);
      checkbox.addEventListener('change', () => toggle(todo.id, checkbox.checked));

      row.querySelector('[data-testid="title"]').textContent = todo.title;

      const del = row.querySelector('[data-testid="delete"]');
      del.setAttribute('aria-label', `Delete "${todo.title}"`);
      del.addEventListener('click', () => confirmThenRemove(todo));

      return row;
    })
  );

  els.empty.hidden = rows.length > 0;
  els.empty.textContent =
    todos.length === 0
      ? 'Nothing here yet.'
      : filter === 'active'
        ? 'All done. Nice.'
        : 'No completed items.';

  const left = todos.filter((t) => !t.done).length;
  const done = todos.length - left;
  els.summary.textContent =
    todos.length === 0
      ? 'No to-dos'
      : `${left} ${left === 1 ? 'item' : 'items'} left · ${done} done`;

  els.clearDone.hidden = done === 0;
}

// ------------------------------------------------------------------ actions

async function add(title) {
  const trimmed = title.trim();
  if (!trimmed) return;
  const todo = await api('', { method: 'POST', body: JSON.stringify({ title: trimmed }) });
  todos.push(todo);
  render();
}

async function toggle(id, done) {
  const updated = await api(`/${id}`, { method: 'PATCH', body: JSON.stringify({ done }) });
  const i = todos.findIndex((t) => t.id === id);
  if (i !== -1) todos[i] = updated;
  render();
}

// Deleting is the one irreversible action here, so it goes through a modal
// confirmation. Resolves true only if the user explicitly picks Delete —
// Cancel, Escape and a backdrop click all resolve false.
function askToDelete(todo) {
  if (els.confirm.open) return Promise.resolve(false);

  els.confirmItem.textContent = todo.title;
  els.confirm.returnValue = '';

  const opener = document.activeElement;
  els.confirm.showModal();
  els.confirmCancel.focus(); // default to the safe choice

  return new Promise((resolve) => {
    els.confirm.addEventListener(
      'close',
      () => {
        if (opener && opener.isConnected) opener.focus();
        resolve(els.confirm.returnValue === 'delete');
      },
      { once: true }
    );
  });
}

async function confirmThenRemove(todo) {
  if (!(await askToDelete(todo))) return;
  try {
    await remove(todo.id);
  } catch (err) {
    els.summary.textContent = `Could not delete: ${err.message}`;
  }
}

async function remove(id) {
  await api(`/${id}`, { method: 'DELETE' });
  todos = todos.filter((t) => t.id !== id);
  render();
}

async function clearDone() {
  const finished = todos.filter((t) => t.done);
  await Promise.all(finished.map((t) => api(`/${t.id}`, { method: 'DELETE' })));
  todos = todos.filter((t) => !t.done);
  render();
}

// -------------------------------------------------------------------- wiring

els.composer.addEventListener('submit', (event) => {
  event.preventDefault();
  const value = els.input.value;
  els.input.value = '';
  els.input.focus();
  add(value).catch((err) => {
    els.input.value = value;
    els.summary.textContent = `Could not add: ${err.message}`;
  });
});

els.clearDone.addEventListener('click', () => {
  clearDone().catch((err) => {
    els.summary.textContent = `Could not clear: ${err.message}`;
  });
});

// A click that lands on the <dialog> itself (rather than its panel) is a click
// on the backdrop — treat it as Cancel.
els.confirm.addEventListener('click', (event) => {
  if (event.target === els.confirm) els.confirm.close('cancel');
});

els.filters.forEach((btn) => {
  btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    els.filters.forEach((b) => b.classList.toggle('is-active', b === btn));
    render();
  });
});

// ---------------------------------------------------------------------- boot

api('')
  .then((data) => {
    todos = data;
    render();
    document.body.dataset.ready = 'true';
  })
  .catch((err) => {
    els.summary.textContent = `Could not load to-dos: ${err.message}`;
  });
