/* ═══════════════════════════════════════════════════
   CANDLE K — Interactive JS
   Cursor glow · Particles · Scroll reveals · Cart drawer
   Card tilt · Counters · Gallery · Payment upload
   ═══════════════════════════════════════════════════ */
'use strict';

const $ = (s, ctx = document) => ctx.querySelector(s);
const $$ = (s, ctx = document) => [...ctx.querySelectorAll(s)];

document.addEventListener('DOMContentLoaded', () => {
  bodyReveal();
  initNav();
  initCursorGlow();
  initParticles();
  initScrollReveal();
  initParallax();
  initCardTilt();
  initCounters();
  initCartDrawer();
  initProductGallery();
  initPayment();
  initQty();
});

/* ─── Page fade-in ─────────────────────────── */
function bodyReveal() {
  document.body.style.cssText = 'opacity:0;transition:opacity .45s ease;';
  requestAnimationFrame(() => document.body.style.opacity = '1');
}

/* ─── Nav scroll shadow + mobile + search ─── */
function initNav() {
  const nav = $('.nav');
  if (!nav) return;
  window.addEventListener('scroll', () => nav.classList.toggle('scrolled', scrollY > 30), { passive: true });

  const toggle = $('.nav__mobile-toggle');
  const links  = $('.nav__links');
  toggle?.addEventListener('click', () => links?.classList.toggle('mobile-open'));
  document.addEventListener('click', e => {
    if (!nav.contains(e.target)) links?.classList.remove('mobile-open');
  });

  const searchBtn = $('.nav__search-toggle');
  const searchWrap = $('.nav__search');
  const searchInput = searchWrap?.querySelector('input');
  searchBtn?.addEventListener('click', () => {
    searchWrap.classList.toggle('open');
    if (searchWrap.classList.contains('open')) searchInput?.focus();
  });
  searchInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && searchInput.value.trim())
      location.href = `/shop?q=${encodeURIComponent(searchInput.value.trim())}`;
    if (e.key === 'Escape') searchWrap.classList.remove('open');
  });

  // Active link
  $$('.nav__links a').forEach(a => {
    if (a.pathname === location.pathname) a.classList.add('active');
  });
}

/* ─── Soft pink cursor glow ────────────────── */
function initCursorGlow() {
  const glow = Object.assign(document.createElement('div'), { id: 'cursor-glow' });
  glow.style.cssText = `position:fixed;pointer-events:none;z-index:9998;
    width:380px;height:380px;border-radius:50%;top:0;left:0;
    background:radial-gradient(circle,rgba(232,160,160,.07) 0%,transparent 65%);
    transform:translate(-50%,-50%);transition:width .4s,height .4s,opacity .3s;`;
  document.body.appendChild(glow);

  let mx = -999, my = -999, gx = -999, gy = -999;
  document.addEventListener('mousemove', e => { mx = e.clientX; my = e.clientY; });
  document.addEventListener('mouseleave', () => glow.style.opacity = '0');
  document.addEventListener('mouseenter', () => glow.style.opacity = '1');

  const lerp = (a, b, t) => a + (b - a) * t;
  const tick = () => {
    gx = lerp(gx, mx, .07); gy = lerp(gy, my, .07);
    glow.style.left = gx + 'px'; glow.style.top = gy + 'px';
    requestAnimationFrame(tick);
  };
  tick();

  $$('.product-card,.btn,a').forEach(el => {
    el.addEventListener('mouseenter', () => {
      glow.style.width = glow.style.height = '520px';
      glow.style.background = 'radial-gradient(circle,rgba(232,160,160,.13) 0%,transparent 65%)';
    });
    el.addEventListener('mouseleave', () => {
      glow.style.width = glow.style.height = '380px';
      glow.style.background = 'radial-gradient(circle,rgba(232,160,160,.07) 0%,transparent 65%)';
    });
  });
}

/* ─── Hero floating particles ──────────────── */
function initParticles() {
  const hero = $('.hero');
  if (!hero) return;
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:1;';
  hero.style.position = 'relative';
  hero.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  let W, H, pts = [];

  const mkPt = () => ({
    x: Math.random() * W, y: H + 10,
    r: Math.random() * 2.5 + .8,
    dx: (Math.random() - .5) * .5,
    dy: -(Math.random() * .6 + .2),
    a: Math.random() * .55 + .1,
    flick: Math.random() * Math.PI * 2,
    col: Math.random() > .5 ? '232,160,160' : '143,168,130',
  });

  const resize = () => {
    W = canvas.width = hero.offsetWidth;
    H = canvas.height = hero.offsetHeight;
  };

  const draw = () => {
    ctx.clearRect(0, 0, W, H);
    pts.forEach(p => {
      p.flick += .018; p.x += p.dx; p.y += p.dy;
      const a = p.a * (.65 + .35 * Math.sin(p.flick));
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${p.col},${a})`; ctx.fill();
      if (p.y < -10 || p.x < -10 || p.x > W + 10) Object.assign(p, mkPt());
    });
    requestAnimationFrame(draw);
  };

  resize();
  pts = Array.from({ length: 32 }, mkPt).map(p => ({ ...p, y: Math.random() * H }));
  draw();
  window.addEventListener('resize', resize);
}

/* ─── Scroll reveal animations ─────────────── */
function initScrollReveal() {
  const css = `
    .rv { opacity:0; transform:translateY(28px); transition:opacity .7s cubic-bezier(.4,0,.2,1),transform .7s cubic-bezier(.4,0,.2,1); }
    .rv.in { opacity:1; transform:none; }
    .rv-l { opacity:0; transform:translateX(-28px); transition:opacity .7s ease,transform .7s ease; }
    .rv-l.in { opacity:1; transform:none; }
    .rv-r { opacity:0; transform:translateX(28px); transition:opacity .7s ease,transform .7s ease; }
    .rv-r.in { opacity:1; transform:none; }
    .rv-s { opacity:0; transform:scale(.93); transition:opacity .6s ease,transform .6s ease; }
    .rv-s.in { opacity:1; transform:none; }
  `;
  document.head.appendChild(Object.assign(document.createElement('style'), { textContent: css }));

  $$('.product-card').forEach((el, i) => {
    el.classList.add('rv');
    el.style.transitionDelay = `${(i % 4) * 75}ms`;
  });
  $$('.testimonial-card').forEach((el, i) => {
    el.classList.add('rv');
    el.style.transitionDelay = `${i * 90}ms`;
  });
  $$('.section-title,.section-label').forEach(el => el.classList.add('rv'));
  $$('.about__visual').forEach(el => el.classList.add('rv-l'));
  $$('.about__feature').forEach((el, i) => {
    el.classList.add('rv-s');
    el.style.transitionDelay = `${i * 80}ms`;
  });

  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); obs.unobserve(e.target); } });
  }, { threshold: .1 });
  $$('.rv,.rv-l,.rv-r,.rv-s').forEach(el => obs.observe(el));
}

/* ─── Hero parallax ────────────────────────── */
function initParallax() {
  const hc = $('.hero__content');
  const hv = $('.hero__visual');
  if (!hc) return;
  window.addEventListener('scroll', () => {
    const y = scrollY;
    if (y < innerHeight) {
      hc.style.transform = `translateY(${y * .1}px)`;
      if (hv) hv.style.transform = `translateY(${y * .05}px)`;
    }
  }, { passive: true });
}

/* ─── 3-D card tilt ────────────────────────── */
function initCardTilt() {
  $$('.product-card').forEach(card => {
    card.addEventListener('mousemove', e => {
      const r = card.getBoundingClientRect();
      const rx = ((e.clientY - r.top  - r.height / 2) / r.height) * 7;
      const ry = ((e.clientX - r.left - r.width  / 2) / r.width)  * -7;
      card.style.transform = `translateY(-5px) rotateX(${rx}deg) rotateY(${ry}deg)`;
      card.style.transformStyle = 'preserve-3d';
    });
    card.addEventListener('mouseleave', () => {
      card.style.transition = 'transform .5s ease,box-shadow .3s,border-color .3s';
      card.style.transform = '';
      setTimeout(() => card.style.transition = '', 500);
    });
  });
}

/* ─── Animated counters ─────────────────────── */
function initCounters() {
  $$('.about__feature-num').forEach(el => {
    const raw = el.textContent.trim();
    const num = parseFloat(raw.replace(/[^\d.]/g, ''));
    const sfx = raw.replace(/[\d.]/g, '');
    if (!num) return;
    let done = false;
    const obs = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting && !done) {
          done = true;
          const t0 = performance.now();
          const dur = 1300;
          const tick = now => {
            const p = Math.min((now - t0) / dur, 1);
            const v = 1 - Math.pow(1 - p, 3);
            el.textContent = Math.round(num * v) + sfx;
            if (p < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
          obs.disconnect();
        }
      });
    }, { threshold: .5 });
    obs.observe(el);
  });
}

/* ─── Cart drawer + AJAX add-to-cart ────────── */
function initCartDrawer() {
  // Inject styles
  document.head.appendChild(Object.assign(document.createElement('style'), { textContent: `
    #ck-overlay { position:fixed;inset:0;background:rgba(44,42,39,.45);z-index:800;opacity:0;
      pointer-events:none;transition:opacity .35s ease;backdrop-filter:blur(3px); }
    #ck-overlay.open { opacity:1;pointer-events:all; }
    #ck-drawer { position:fixed;right:0;top:0;bottom:0;width:420px;max-width:95vw;
      background:var(--ivory);z-index:801;transform:translateX(100%);
      transition:transform .42s cubic-bezier(.4,0,.2,1);
      display:flex;flex-direction:column;box-shadow:-10px 0 48px rgba(44,42,39,.16); }
    #ck-drawer.open { transform:translateX(0); }
    .ck-toast { position:fixed;bottom:28px;right:28px;z-index:900;
      background:var(--charcoal);color:var(--cream);
      padding:14px 22px;border-left:3px solid var(--pink);
      border-radius:0 4px 4px 0;font-size:.84rem;max-width:290px;
      transform:translateX(calc(100% + 36px));
      transition:transform .42s cubic-bezier(.34,1.56,.64,1);
      box-shadow:0 8px 28px rgba(44,42,39,.22); }
    .ck-toast.show { transform:translateX(0); }
    @keyframes cartPop { 0%,100%{transform:scale(1)} 40%{transform:scale(1.45) rotate(-8deg)} 70%{transform:scale(.9) rotate(4deg)} }
    .cart-pop { animation:cartPop .5s ease; }
    @keyframes btnSpin { to{transform:rotate(360deg)} }
  ` }));

  const overlay = Object.assign(document.createElement('div'), { id: 'ck-overlay' });
  const drawer  = document.createElement('div');
  drawer.id = 'ck-drawer';
  drawer.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:22px 26px;border-bottom:1px solid var(--line-lt);flex-shrink:0;">
      <h3 style="font-family:var(--font-serif);font-size:1.45rem;font-style:italic;color:var(--charcoal);">Your Cart</h3>
      <button id="ck-close" style="background:none;border:none;font-size:1.6rem;color:var(--charcoal-lt);cursor:pointer;line-height:1;padding:0 4px;">×</button>
    </div>
    <div id="ck-items" style="flex:1;overflow-y:auto;padding:18px 26px;"></div>
    <div style="flex-shrink:0;border-top:1px solid var(--line-lt);padding:18px 26px;">
      <div id="ck-total" style="display:flex;justify-content:space-between;align-items:baseline;font-family:var(--font-serif);font-size:1.5rem;font-style:italic;color:var(--charcoal);margin-bottom:14px;"></div>
      <a href="/cart" class="btn btn-outline btn-sm" style="width:100%;display:block;text-align:center;margin-bottom:8px;">View full cart</a>
      <a href="/checkout" class="btn btn-primary" style="width:100%;display:block;text-align:center;">Checkout →</a>
    </div>`;

  const toast = Object.assign(document.createElement('div'), { className: 'ck-toast' });
  document.body.append(overlay, drawer, toast);

  const open  = () => { overlay.classList.add('open'); drawer.classList.add('open'); };
  const close = () => { overlay.classList.remove('open'); drawer.classList.remove('open'); };
  overlay.addEventListener('click', close);
  $('#ck-close', drawer)?.addEventListener('click', close);

  // Cart icon → drawer (unless already on cart page)
  const cartIcon = $('.nav__cart');
  if (cartIcon && !location.pathname.startsWith('/cart') && !location.pathname.startsWith('/checkout')) {
    cartIcon.addEventListener('click', e => {
      e.preventDefault();
      loadDrawer().then(open);
    });
  }

  const showToast = name => {
    toast.innerHTML = `<strong style="color:var(--pink);">${name}</strong> added ✓`;
    toast.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.classList.remove('show'), 3000);
  };

  // AJAX add-to-cart
  $$('.add-to-cart-form').forEach(form => {
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const btn = form.querySelector('[type=submit]');
      const orig = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = `<span style="display:inline-block;animation:btnSpin .6s linear infinite;font-size:1rem;">◌</span>`;

      try {
        const res  = await fetch('/cart/add', { method: 'POST', body: new FormData(form) });
        const data = await res.json();
        if (data.success) {
          $$('.nav__cart-count').forEach(el => {
            el.textContent = data.cart_count;
            el.classList.toggle('hidden', data.cart_count === 0);
          });
          const ic = $('.nav__cart');
          ic?.classList.add('cart-pop');
          setTimeout(() => ic?.classList.remove('cart-pop'), 600);

          showToast(form.dataset.name || 'Item');

          btn.style.cssText = 'background:var(--leaf);border-color:var(--leaf);color:white;';
          btn.innerHTML = '✓ Added';
          setTimeout(() => {
            btn.style.cssText = ''; btn.innerHTML = orig; btn.disabled = false;
          }, 2000);
        }
      } catch { btn.innerHTML = orig; btn.disabled = false; }
    });
  });

  async function loadDrawer() {
    try {
      const data = await fetch('/cart/data').then(r => r.json());
      const el   = $('#ck-items', drawer);
      const tot  = $('#ck-total', drawer);
      if (!data.items.length) {
        el.innerHTML = `<div style="text-align:center;padding:60px 0;color:var(--charcoal-lt);">
          <div style="font-size:3rem;margin-bottom:14px;">🕯️</div>
          <p style="font-family:var(--font-serif);font-size:1.1rem;font-style:italic;">Your cart is empty</p>
          <a href="/shop" style="color:var(--pink-deep);font-size:.84rem;display:block;margin-top:10px;">Browse candles →</a>
        </div>`;
        tot.innerHTML = '';
      } else {
        el.innerHTML = data.items.map(it => `
          <div style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line-lt);">
            <div style="width:70px;height:70px;background:var(--ivory-dark);border-radius:4px;flex-shrink:0;overflow:hidden;">
              ${it.image
                ? `<img src="/uploads/products/${it.image}" style="width:100%;height:100%;object-fit:cover;" alt="${it.name}">`
                : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:1.5rem;">🕯️</div>`}
            </div>
            <div style="flex:1;min-width:0;">
              <div style="font-family:var(--font-serif);font-style:italic;color:var(--charcoal);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${it.name}</div>
              <div style="font-size:.8rem;color:var(--charcoal-lt);">× ${it.qty}</div>
              <div style="font-family:var(--font-serif);color:var(--charcoal);margin-top:3px;">${Math.round(it.price * it.qty)} EGP</div>
            </div>
          </div>`).join('');
        tot.innerHTML = `<span>Total</span><span>${Math.round(data.total)} EGP</span>`;
      }
    } catch {}
  }
}

/* ─── Product image gallery + zoom ────────── */
function initProductGallery() {
  const main   = $('.gallery__main img');
  const thumbs = $$('.gallery__thumb');
  if (!main || !thumbs.length) return;
  main.style.transition = 'opacity .22s ease, transform .22s ease';

  thumbs.forEach(t => {
    t.addEventListener('click', () => {
      const src = t.querySelector('img')?.src;
      if (!src) return;
      main.style.opacity = '0'; main.style.transform = 'scale(1.04)';
      setTimeout(() => {
        main.src = src;
        main.style.opacity = '1'; main.style.transform = '';
      }, 200);
      thumbs.forEach(x => x.classList.remove('active'));
      t.classList.add('active');
    });
  });

  // Zoom-follow on hover
  const wrap = $('.gallery__main');
  wrap?.addEventListener('mousemove', e => {
    const r = wrap.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width  - .5) * 7;
    const y = ((e.clientY - r.top)  / r.height - .5) * 7;
    main.style.transform = `scale(1.07) translate(${-x}px,${-y}px)`;
  });
  wrap?.addEventListener('mouseleave', () => { main.style.transform = ''; });
}

/* ─── Payment: toggle + drag-drop upload ──── */
function initPayment() {
  const methods    = $$('.pay-method');
  const infoBox    = $('#instapay-info');
  const uploadZone = $('#upload-zone');

  methods.forEach(m => {
    const radio = m.querySelector('input[type=radio]');
    m.addEventListener('click', () => {
      methods.forEach(x => x.classList.remove('active'));
      m.classList.add('active');
      radio.checked = true;
      const ip = radio.value === 'instapay';
      infoBox?.classList.toggle('show', ip);
      uploadZone?.classList.toggle('show', ip);
    });
    if (radio?.checked) {
      m.classList.add('active');
      if (radio.value === 'instapay') {
        infoBox?.classList.add('show');
        uploadZone?.classList.add('show');
      }
    }
  });

  // Drag-drop
  const fileInput = $('#screenshot-input');
  const preview   = $('#payment-preview');
  if (!uploadZone || !fileInput) return;

  uploadZone.addEventListener('click', () => fileInput.click());
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f && (f.type.startsWith('image/') || f.type === 'application/pdf')) applyFile(f);
  });
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) applyFile(fileInput.files[0]); });

  function applyFile(file) {
    // Sync to real input
    const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files;

    uploadZone.querySelector('.upload-text').innerHTML = `
      <div style="font-size:2rem;margin-bottom:6px;">✅</div>
      <strong style="color:var(--leaf);">${file.name}</strong><br>
      <span style="font-size:.75rem;color:var(--charcoal-lt);">Click to change</span>`;
    uploadZone.style.borderColor = 'var(--leaf)';

    if (file.type.startsWith('image/') && preview) {
      const reader = new FileReader();
      reader.onload = ev => {
        preview.style.display = 'block';
        preview.innerHTML = `<img src="${ev.target.result}" style="max-width:100%;max-height:220px;object-fit:contain;border-radius:6px;">
          <p style="font-size:.78rem;color:var(--charcoal-lt);margin-top:8px;">Preview — ${Math.round(file.size/1024)} KB</p>`;
      };
      reader.readAsDataURL(file);
    }
  }
}

/* ─── Qty controls ─────────────────────────── */
function initQty() {
  $$('.qty-input').forEach(wrap => {
    const inp = wrap.querySelector('input');
    if (!inp) return;
    const min = +inp.min || 1, max = +inp.max || 999;
    wrap.querySelector('.qty-minus')?.addEventListener('click', () => {
      inp.value = Math.max(min, +inp.value - 1);
      inp.dispatchEvent(new Event('change'));
    });
    wrap.querySelector('.qty-plus')?.addEventListener('click', () => {
      inp.value = Math.min(max, +inp.value + 1);
      inp.dispatchEvent(new Event('change'));
    });
  });
  // Cart page auto-submit
  $$('.cart-qty-form .qty-input input').forEach(inp => {
    inp.addEventListener('change', () => inp.closest('form')?.submit());
  });
}
