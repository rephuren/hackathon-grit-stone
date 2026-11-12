/* picPac «Момент» — анимации слоя оформления v2 (без библиотек).
   Включение/откат: window.ppDesign.set('classic' | 'v2'). По умолчанию берётся window.PICPAC_DESIGN из config.js,
   затем выбор пользователя (localStorage pp_design), затем ?design=classic|v2 в адресе. */
(function () {
  var root = document.documentElement;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function pick() {
    var q = (location.search.match(/[?&]design=(classic|v2|v3)/) || [])[1];
    var saved = null;
    try { saved = localStorage.getItem('pp_design'); } catch (e) { /* приватный режим */ }
    return q || saved || window.PICPAC_DESIGN || 'v3';
  }
  var mode = pick();
  if (mode === 'v2' || mode === 'v3') root.classList.add('v2');
  if (mode === 'v3') root.classList.add('v3');

  window.ppDesign = {
    get: function () { return root.classList.contains('v3') ? 'v3' : root.classList.contains('v2') ? 'v2' : 'classic'; },
    set: function (m) { try { localStorage.setItem('pp_design', m); } catch (e) { /* ignore */ } location.reload(); },
  };
  if (mode === 'classic') { window.ppConfetti = function () {}; return; }

  /* ---------- заставка на главной ---------- */
  function decorateHero(hero) {
    if (hero.dataset.v2 || hero.classList.contains('v3h')) return;
    hero.dataset.v2 = '1';
    var h1 = hero.querySelector('h1');
    if (h1) {  // строки заголовка выезжают снизу по очереди
      var parts = h1.innerHTML.split(/<br\s*\/?>/i);
      h1.innerHTML = parts.map(function (t, i) { return '<span class="ln"><span style="--l:' + i + '">' + t + '</span></span>'; }).join('');
    }
    var prints = document.createElement('div');
    prints.className = 'v2-prints';
    prints.setAttribute('aria-hidden', 'true');
    prints.innerHTML = '<i></i><i></i><i></i>';
    hero.insertBefore(prints, hero.querySelector('.m-robot'));
    [['right:66px;width:96px'], ['right:8px;width:70px;bottom:30px']].forEach(function (g) {  // тени под роботом и котом
      var sh = document.createElement('span');
      sh.className = 'v2-ground';
      sh.setAttribute('aria-hidden', 'true');
      sh.style.cssText = g[0];
      hero.insertBefore(sh, prints);
    });
    [[18, 30, 0], [62, 18, 1.1], [84, 52, 2.2], [40, 70, .6]].forEach(function (p) {
      var s = document.createElement('span');
      s.className = 'v2-spark';
      s.setAttribute('aria-hidden', 'true');
      s.style.cssText = 'right:' + p[0] + '%;top:' + p[1] + '%;animation-delay:' + p[2] + 's';
      hero.appendChild(s);
    });
  }

  /* ---------- картинки: мерцание до загрузки ---------- */
  function watchImages(scope) {
    scope.querySelectorAll('img').forEach(function (img) {
      if (img.dataset.v2w) return;
      img.dataset.v2w = '1';
      if (img.complete && img.naturalWidth) return;
      img.classList.add('v2-wait');
      var done = function () { img.classList.remove('v2-wait'); img.classList.add('v2-ok'); };
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', function () { img.classList.remove('v2-wait'); }, { once: true });
    });
  }

  /* ---------- новый экран: каскадное появление ---------- */
  function enter(main) {
    if (reduce || !main || main.dataset.v2) return;
    main.dataset.v2 = '1';
    Array.prototype.forEach.call(main.children, function (el, i) { el.style.setProperty('--i', Math.min(i, 10)); });
    main.querySelectorAll('.stack').forEach(function (st) {
      Array.prototype.forEach.call(st.children, function (el, j) { el.style.setProperty('--j', Math.min(j, 8)); });
    });
    main.classList.add('enter');
    setTimeout(function () { main.classList.remove('enter'); }, 1600);  // после входа анимации не мешают перерисовкам
  }

  function scan() {
    var app = document.getElementById('app');
    if (!app) return;
    enter(app.querySelector('main'));
    var hero = app.querySelector('.hero');
    if (hero) decorateHero(hero);
    watchImages(app);
  }

  function start() {
    var app = document.getElementById('app');
    if (!app) return;
    new MutationObserver(function () { scan(); }).observe(app, { childList: true, subtree: true });
    scan();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

  /* ---------- рябь от нажатия на кнопки ---------- */
  document.addEventListener('pointerdown', function (e) {
    if (reduce) return;
    var b = e.target.closest && e.target.closest('.btn');
    if (!b || b.disabled) return;
    var r = b.getBoundingClientRect(), d = Math.max(r.width, r.height);
    var s = document.createElement('span');
    s.className = 'v2-ripple';
    s.style.cssText = 'width:' + d + 'px;height:' + d + 'px;left:' + (e.clientX - r.left - d / 2) + 'px;top:' + (e.clientY - r.top - d / 2) + 'px';
    b.appendChild(s);
    setTimeout(function () { s.remove(); }, 600);
  }, { passive: true });

  /* ---------- конфетти при успешном заказе ---------- */
  window.ppConfetti = function () {
    if (reduce) return;
    var c = document.createElement('canvas');
    c.className = 'v2-confetti';
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = innerWidth * dpr; c.height = innerHeight * dpr;
    document.body.appendChild(c);
    var ctx = c.getContext('2d'); ctx.scale(dpr, dpr);
    var colors = ['#ffda24', '#171717', '#22c3dd', '#ff7a59', '#ffffff', '#27b35a'];
    var parts = [];
    for (var i = 0; i < 140; i++) {
      parts.push({ x: innerWidth / 2 + (Math.random() - .5) * 80, y: innerHeight * .35, vx: (Math.random() - .5) * 11,
        vy: -Math.random() * 13 - 4, w: 6 + Math.random() * 6, h: 8 + Math.random() * 8, r: Math.random() * 6,
        vr: (Math.random() - .5) * .35, c: colors[i % colors.length] });
    }
    var t0 = performance.now();
    (function frame(t) {
      var age = t - t0;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      parts.forEach(function (p) {
        p.vy += .35; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save(); ctx.globalAlpha = Math.max(0, 1 - age / 2600);
        ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r)));
        ctx.restore();
      });
      if (age < 2700) requestAnimationFrame(frame); else c.remove();
    })(t0);
  };
})();
