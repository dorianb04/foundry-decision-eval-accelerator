// DOM snapshot: visible interactive elements + visible text lines, no screenshots.
// Adapted in spirit from browser-use/jev-ultrafast snapshot.js (MIT), simplified.
// Evaluated as an expression; returns a plain JSON object.
(() => {
  if (!document.body) return null;
  const C = (window.__cu ||= {ids: new WeakMap(), nodes: new Map(), next: 1});
  const identity = e => {
    if (!C.ids.has(e)) C.ids.set(e, C.next++);
    const id = C.ids.get(e); C.nodes.set(id, e); return id;
  };
  for (const [id, e] of C.nodes) if (!e.isConnected) C.nodes.delete(id);
  const safe = e => !['password', 'file', 'hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const name = e => {
    const ref = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => clean(document.getElementById(id)?.textContent)).filter(Boolean).join(' ');
    if (ref) return ref;
    if (e.getAttribute('aria-label')) return clean(e.getAttribute('aria-label'));
    const labels = [...(e.labels || [])].map(l => clean(l.textContent)).filter(Boolean).join(' ');
    if (labels) return labels;
    if (['button', 'submit', 'reset'].includes(e.type) && e.value) return clean(e.value);
    if (e.tagName !== 'INPUT' && e.tagName !== 'SELECT' && e.tagName !== 'TEXTAREA') {
      const t = clean(e.innerText || e.textContent);
      if (t) return t;
    }
    return clean(e.getAttribute('alt') || e.getAttribute('title') || e.getAttribute('placeholder'));
  };
  const roles = ['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'combobox',
    'textbox', 'searchbox', 'spinbutton'];
  const selector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],' +
    roles.map(r => `[role="${r}"]`).join(',');
  const role = e => {
    const explicit = e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName === 'BUTTON' || e.tagName === 'SUMMARY') return 'button';
    if (e.tagName === 'A') return 'link';
    if (e.tagName === 'SELECT') return 'select';
    if (e.tagName === 'TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName === 'INPUT') {
      if (['checkbox', 'radio'].includes(e.type)) return e.type;
      if (['button', 'submit', 'reset', 'image'].includes(e.type)) return 'button';
      if (e.type === 'search') return 'searchbox';
      if (e.type === 'number') return 'spinbutton';
      return 'textbox';
    }
    return null;
  };

  const elements = [];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled')) continue;
    const r = e.getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    const rl = role(e);
    if (!rl || r.width <= 0 || r.height <= 0 || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    const el = {node: identity(e), role: rl, name: name(e) || rl};
    if (e.tagName === 'A') {
      try { el.href = new URL(e.href).pathname + new URL(e.href).search; } catch (_) {}
    }
    if (rl === 'button' && (e.type === 'submit' || (e.tagName === 'BUTTON' && !e.getAttribute('type'))) && e.form) {
      el.submits = true;
    }
    if (['checkbox', 'radio'].includes(rl)) el.checked = !!e.checked;
    if (rl === 'select') {
      el.options = [...e.options].filter(o => !o.disabled)
        .map(o => ({value: o.value, label: clean(o.label || o.textContent), selected: o.selected}));
      el.value = (el.options.find(o => o.selected) || {label: ''}).label;
    } else if (['textbox', 'searchbox', 'spinbutton'].includes(rl)) {
      el.value = String(e.value ?? '').slice(0, 200);
      if (e.required) el.required = true;
    }
    elements.push(el);
  }

  const lines = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let n, total = 0;
  while ((n = walker.nextNode()) && total < 3000) {
    const v = clean(n.textContent), p = n.parentElement;
    if (!v || !p || p.closest('script,style,noscript,template,option,textarea') || !visible(p)) continue;
    range.selectNodeContents(n);
    const r = range.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth) {
      lines.push(v.slice(0, 300)); total += v.length;
    }
  }
  const height = document.documentElement.scrollHeight;
  const scroll = {y: Math.round(scrollY), height, vh: innerHeight,
    can_down: scrollY + innerHeight < height - 2, can_up: scrollY > 0};
  // Freshness fingerprint: identity, meaning and scroll position, not geometry.
  const fp = JSON.stringify([location.href, document.title, Math.round(scrollY), lines,
    elements.map(e => [e.node, e.role, e.name, e.value ?? null, e.checked ?? null,
      e.options ? e.options.find(o => o.selected)?.value : null])]);
  return {url: location.pathname + location.search, title: document.title, text: lines,
    elements, scroll, fp};
})()
