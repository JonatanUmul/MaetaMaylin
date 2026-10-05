(() => {
  'use strict';
  const key = 'mimo-cart-v1';
  let products = new Map();
  let busy = false;
  let blocked = true;
  let toastTimer;
  const toast = message => {
    const element = document.getElementById('toast'); element.textContent = message; element.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { element.hidden = true; }, 3500);
  };
  const load = () => {
    try {
      const items = JSON.parse(localStorage.getItem(key) || '[]');
      if (!Array.isArray(items)) return [];
      const seen = new Set();
      return items.filter(i => Number.isInteger(i.product_id) && i.product_id > 0 && Number.isInteger(i.quantity) && i.quantity > 0 && i.quantity <= 100 && !seen.has(i.product_id) && seen.add(i.product_id)).slice(0, 100).map(i => ({product_id: i.product_id, quantity: i.quantity}));
    } catch { return []; }
  };
  const count = () => { document.getElementById('cart-count').textContent = load().reduce((n, i) => n + i.quantity, 0); };
  const save = items => {
    try { localStorage.setItem(key, JSON.stringify(items)); count(); return true; }
    catch { toast('El navegador no permite guardar el solicitud. Habilita el almacenamiento local.'); return false; }
  };
  const node = (tag, className, text) => { const e = document.createElement(tag); if (className) e.className = className; if (text !== undefined) e.textContent = text; return e; };
  count();
  document.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => {
    const id = Number(button.dataset.add), limit = 100;
    const quantity = button.dataset.quantity ? Number(document.getElementById(button.dataset.quantity).value) : 1;
    const items = load(), existing = items.find(i => i.product_id === id);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity + (existing?.quantity || 0) > limit) { toast('Puedes solicitar de 1 a 100 unidades por producto. Para cantidades mayores, escríbenos.'); return; }
    if (!existing && items.length >= 100) { toast('Tu solicitud admite hasta 100 productos diferentes.'); return; }
    if (existing) existing.quantity += quantity; else items.push({product_id: id, quantity});
    if (save(items)) { sessionStorage.removeItem('mimo-receipt'); toast('Producto añadido a tu solicitud'); refresh(); }
  }));
  document.querySelector('.filters')?.addEventListener('submit', event => {
    event.currentTarget.querySelectorAll('input,select').forEach(e => { if (!e.value.trim()) e.disabled = true; });
  });
  const container = document.getElementById('cart-items');
  const submit = document.getElementById('submit-order');
  const link = document.getElementById('checkout-link');
  const render = () => {
    if (!container) return;
    container.replaceChildren();
    const items = load(); let units = 0; blocked = !items.length;
    if (!items.length) {
      const empty = node('div', 'empty-state'); empty.append(node('h3', '', 'Tu solicitud espera su primer producto.'), node('p', '', 'Explora la colección y elige algo especial.'));
      const go = node('a', 'button', 'Explorar colección'); go.href = '/#coleccion'; empty.append(go); container.append(empty);
    }
    items.forEach(item => {
      const product = products.get(item.product_id); const valid = Boolean(product);
      if (!valid) blocked = true;
      const row = node('div', 'cart-row');
      if (product) { const img = node('img'); img.src = product.image_url; img.alt = product.title; row.append(img); units += item.quantity; }
      const info = node('div', 'cart-row-info'); info.append(node('h3', '', product?.title || 'Producto no disponible'));
      info.append(node('p', valid ? '' : 'error', product ? 'Por encargo · Precio a cotizar' : 'Retira este producto para continuar.'));
      const controls = node('div', 'cart-controls');
      const input = node('input'); input.type = 'number'; input.min = '1'; input.max = '100'; input.value = item.quantity; input.disabled = !product || busy; input.setAttribute('aria-label', 'Cantidad de ' + (product?.title || 'producto'));
      input.addEventListener('change', () => {
        const quantity = Number(input.value);
        if (!Number.isInteger(quantity) || quantity < 1 || quantity > Number(input.max)) { input.value = item.quantity; toast('Ingresa una cantidad entre 1 y 100.'); return; }
        const current = load(); const found = current.find(i => i.product_id === item.product_id); if (found) found.quantity = quantity;
        if (save(current)) render();
      });
      const remove = node('button', 'remove-button', 'Quitar'); remove.type = 'button'; remove.disabled = busy;
      remove.addEventListener('click', () => { if (save(load().filter(i => i.product_id !== item.product_id))) render(); });
      controls.append(input, remove); info.append(controls); row.append(info); container.append(row);
    });
    document.getElementById('cart-total').textContent = String(units);
    if (submit) submit.disabled = blocked || busy;
    if (link) { link.setAttribute('aria-disabled', String(blocked)); link.classList.toggle('disabled', blocked); }
  };
  link?.addEventListener('click', event => { if (blocked) event.preventDefault(); });
  async function refresh() {
    if (!container || busy) return;
    const items = load();
    if (!items.length) { products.clear(); render(); return; }
    if (submit) submit.disabled = true;
    if (link) { blocked = true; link.classList.add('disabled'); }
    try {
      const params = new URLSearchParams(); items.forEach(i => params.append('ids', i.product_id));
      const response = await fetch('/api/catalog/products?' + params);
      if (!response.ok) throw new Error('catalog');
      const data = await response.json(); products = new Map(data.map(p => [p.id, p])); render();
    } catch {
      blocked = true; container.replaceChildren(node('p', 'error', 'No pudimos actualizar el solicitud.'));
      const retry = node('button', 'button', 'Reintentar'); retry.type = 'button'; retry.addEventListener('click', refresh); container.append(retry);
      document.getElementById('cart-total').textContent = '—';
    }
  }
  window.addEventListener('storage', event => { if (event.key === key) { count(); refresh(); } });
  const form = document.getElementById('checkout-form');
  function requestKey() {
    // getRandomValues also works on HTTP LAN origins; randomUUID requires HTTPS.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function showReceipt(receipt) {
    document.getElementById('checkout-content').hidden = true;
    document.getElementById('receipt').hidden = false;
    document.getElementById('receipt-id').textContent = 'Solicitud ' + (receipt.reference || 'recibida');
  }
  form?.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || blocked) return;
    const error = document.getElementById('checkout-error'); error.hidden = true;
    const data = Object.fromEntries(new FormData(form)); Object.keys(data).forEach(k => { data[k] = data[k].trim(); }); data.items = load();
    busy = true; submit.disabled = true; submit.textContent = 'Enviando tu solicitud…'; render();
    try {
      const payload = JSON.stringify(data);
      let attempt; try { attempt = JSON.parse(sessionStorage.getItem('mimo-attempt')); } catch { attempt = null; }
      if (!attempt || attempt.payload !== payload) { attempt = {payload, key: requestKey()}; sessionStorage.setItem('mimo-attempt', JSON.stringify(attempt)); }
      const response = await fetch('/api/checkout', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': attempt.key}, body: payload});
      const body = await response.json();
      if (!response.ok) {
        const message = typeof body.detail === 'string' ? body.detail : 'Revisa los datos ingresados, el teléfono y las cantidades.';
        if (response.status === 409 || response.status === 422) sessionStorage.removeItem('mimo-attempt');
        throw new Error(message);
      }
      save([]); sessionStorage.removeItem('mimo-attempt');
      sessionStorage.setItem('mimo-receipt', JSON.stringify(body)); showReceipt(body);
    } catch (e) { error.textContent = e.message === 'Failed to fetch' ? 'No pudimos verificar la solicitud. Reintenta sin cambiar los datos para evitar duplicados.' : e.message; error.hidden = false; }
    finally { busy = false; submit.textContent = 'Solicitar cotización →'; await refresh(); }
  });
  if (container) refresh();
  if (form && !load().length) { try { const receipt = JSON.parse(sessionStorage.getItem('mimo-receipt')); if (receipt?.id) showReceipt(receipt); } catch { /* No saved receipt. */ } }
})();
