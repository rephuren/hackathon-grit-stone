(function () {
'use strict';
/* picPac «Момент» — фронтенд без сборки. Работает и в Telegram Mini App, и в обычном мобильном браузере. */
let tg = null;
/* Данные запуска из Telegram лежат в адресе (#tgWebAppData=...). Берём их сразу, до работы роутера, и не зависим от того,
   успел ли загрузиться скрипт telegram.org: на мобильном интернете он может грузиться очень долго. */
const tgLaunch = (() => { try { return new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') || ''; } catch { return ''; } })();
/* SDK Telegram подгружаем отдельно и не дольше 1,5 с: если telegram.org недоступен, приложение всё равно запустится. */
function loadTelegram() {
  return new Promise((resolve) => {
    const done = () => { tg = (window.Telegram && window.Telegram.WebApp) || null; resolve(); };
    if (window.Telegram && window.Telegram.WebApp) return done();  // скрипт уже подключён в index.html
    const s = document.createElement('script');
    s.src = 'https://telegram.org/js/telegram-web-app.js'; s.async = true; s.onload = done; s.onerror = done;
    document.head.appendChild(s);
    setTimeout(done, 800);
  });
}
/* Слой платформы: всё, что зависит от оболочки (Telegram, обычный браузер, нативная обёртка), собрано здесь.
   Остальной код про оболочку ничего не знает, поэтому перенос в iOS/Android/macOS/Windows не требует переписывать экраны. */
const platform = {
  get kind() {
    if (tg || tgLaunch) return 'telegram';
    return window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform() ? 'native' : 'web';
  },
  initData() { return (tg && tg.initData) || tgLaunch || ''; },  // подпись Telegram для входа без пароля
  ready() {
    if (!tg) return;
    try { tg.ready(); tg.expand(); tg.setHeaderColor('#ffffff'); tg.setBackgroundColor('#ffffff'); } catch { /* старый клиент */ }
  },
  setBack(visible) {  // системная кнопка «назад» (в Telegram), в остальных оболочках есть стрелка в шапке экрана
    if (tg && tg.BackButton) { try { visible ? tg.BackButton.show() : tg.BackButton.hide(); } catch { /* старый клиент */ } }
  },
  onBack(fn) {
    try { if (tg) tg.BackButton.onClick(fn); } catch { /* нет кнопки */ }
    document.addEventListener('backbutton', fn);  // Android-оболочки присылают это событие
  },
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const byn = (k) => (k / 100).toFixed(2).replace('.', ',') + ' BYN';
const fmt = (iso) => (iso ? new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '');
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' }) : '');
const clone = (o) => JSON.parse(JSON.stringify(o));
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return m > 10 && m < 20 ? c : k === 1 ? a : k > 1 && k < 5 ? b : c; };
const V3 = document.documentElement.classList.contains('v3');
/* Персонажи: в v3 — 3D-картинки Робо (WebP с прозрачным фоном), иначе прежние векторные. wave — машет, photo — с фото, think — думает, happy — радуется. */
const MASC3 = { robot: 'robot-wave.webp', cat: 'robot-photo.webp', think: 'robot-think.webp', happy: 'robot-happy.webp' };
const mimg = (n) => (V3 ? `img/${MASC3[n]}` : `img/${n === 'think' || n === 'happy' ? 'robot' : n}.svg`);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* приватный режим */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
const state = { token: store.get('pp_token'), user: null, caps: { bg_removal: false, ai_generate: false }, sim: { offline: false, loseResponse: false }, poll: null, tracked: new Set(), hidden: new Set(), q: new URLSearchParams(), ctx: {}, back: '/', backFn: null, thumbCache: {} };

class NetErr extends Error { constructor() { super('Нет связи с сервером'); } }
class ApiErr extends Error { constructor(status, code, message, detail) { super(message); this.status = status; this.code = code; this.detail = detail; } }

/* Адрес сервера: на сайте и в Telegram пусто (тот же домен). В нативной оболочке (Capacitor и др.) задаётся как
   window.PICPAC_API_BASE в config.js или сохранённым значением pp_api. */
const API_BASE = String(window.PICPAC_API_BASE || (() => { try { return localStorage.getItem('pp_api') || ''; } catch { return ''; } })()).replace(/\/$/, '');
async function rawFetch(path, opts) {
  if (state.sim.offline) throw new NetErr();
  opts.headers = { ...(opts.headers || {}) };
  if (state.token) opts.headers.Authorization = 'Bearer ' + state.token;
  try { return await fetch(API_BASE + '/api' + path, opts); } catch { throw new NetErr(); }
}
async function failFrom(res, path) {
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (res.status === 401 && state.token && path !== '/auth/login') { state.token = null; state.user = null; store.del('pp_token'); }
  const d = data && data.detail && !Array.isArray(data.detail) ? data.detail : null;
  return new ApiErr(res.status, d && d.code, (d && d.message) || 'Проверьте введённые данные (' + res.status + ')', d);
}
async function api(path, { method = 'GET', body, form, headers = {} } = {}) {
  const opts = { method, headers: { ...headers } };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await rawFetch(path, opts);
  if (!res.ok) throw await failFrom(res, path);
  try { return await res.json(); } catch { return null; }
}
async function apiBlob(path, body) {
  const res = await rawFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) throw await failFrom(res, path);
  return URL.createObjectURL(await res.blob());
}
const track = (name) => { if (state.tracked.has(name) || !state.token) return; state.tracked.add(name); api('/events', { method: 'POST', body: { name } }).catch(() => {}); };
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3400); }
const go = (path) => { if (location.hash === '#' + path) route(); else location.hash = '#' + path; };
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));

/* Статусы по ТЗ: принят → модерация → производство → готов/передан в доставку → завершён (+ отклонён с причиной). */
const LABELS = { accepted: 'Принят', moderation: 'Проверка макета', production: 'В производстве', ready: 'Готов, передан в доставку', completed: 'Получен', rejected: 'Отклонён при проверке' };
const FLOW = ['accepted', 'moderation', 'production', 'ready', 'completed'];
const FILTERS = ['none', 'warm', 'film', 'cool', 'bw', 'vivid', 'retro', 'cinema', 'illustration'];
/* Мгновенный предпросмотр на телефоне: CSS-приближение серверных фильтров. Точный кадр всё равно рисует сервер. */
const FILTER_CSS = { none: '', warm: 'sepia(.15) saturate(1.1) hue-rotate(-8deg)', cool: 'hue-rotate(8deg) saturate(.95)', film: 'contrast(1.06) saturate(.85) sepia(.15)', bw: 'grayscale(1) contrast(1.15)', vivid: 'saturate(1.4) contrast(1.1)', retro: 'sepia(.65) contrast(.95) brightness(.95)', cinema: 'contrast(1.12) hue-rotate(-10deg) saturate(1.1)', illustration: 'saturate(1.3) contrast(1.15)' };
const cssOf = (e) => { const a = e.adjust || {}; return [`brightness(${1 + (a.brightness || 0) / 100 * 0.6})`, `contrast(${1 + (a.contrast || 0) / 100 * 0.6})`, `saturate(${1 + (a.saturation || 0) / 100})`, FILTER_CSS[e.filter || 'none']].filter(Boolean).join(' '); };
const FILTER_LABELS = { none: 'Оригинал', warm: 'Тёплый', cool: 'Холодный', film: 'Плёнка', bw: 'Ч/Б', vivid: 'Яркий', retro: 'Ретро', cinema: 'Кино', illustration: 'Рисунок' };

/* ---------- иконки (свои контурные, 24×24) ---------- */
const IC = {
  home: '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="m21 16-5-5-9 9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  cart: '<path d="M3 4h2.5l2.2 10.5h10.2L20 7H6.6"/><circle cx="9.5" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/>',
  user: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.3 18.7a6.6 6.6 0 0 1 11.4 0"/>',
  back: '<path d="m15 5-7 7 7 7"/>', next: '<path d="m9 5 7 7-7 7"/>',
  crop: '<path d="M7 3v14h14M3 7h14v14"/>',
  text: '<path d="M5 7V4h14v3M12 4v16M9 20h6"/>',
  spark: '<path d="m10 3 1.8 5.2L17 10l-5.2 1.8L10 17l-1.8-5.2L3 10l5.2-1.8z"/><path d="m18.5 14 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  filters: '<circle cx="12" cy="8" r="4.5"/><circle cx="8" cy="15" r="4.5"/><circle cx="16" cy="15" r="4.5"/>',
  frame: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
  truck: '<path d="M2 6h11v10H2zM13 9h4l4 4v3h-8"/><circle cx="6.5" cy="17.5" r="1.9"/><circle cx="17" cy="17.5" r="1.9"/>',
  pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.800-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  search: '<circle cx="11" cy="11" r="6.500"/><path d="m20 20-4.200-4.200"/>',
  ticket: '<path d="M4 7h16v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4z"/>',
  gift: '<rect x="4" y="11" width="16" height="9"/><path d="M3 7h18v4H3zM12 7v13M12 7c-2-4.500-6-3-5 0M12 7c2-4.500 6-3 5 0"/>',
  box: '<path d="m12 3 8 4.500v9L12 21l-8-4.500v-9z"/><path d="m4 7.500 8 4.500 8-4.500M12 12v9"/>',
  bag: '<path d="M5 8h14l-1 12H6z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>',
  heart: '<path d="M12 20s-7.500-4.600-7.500-10A4.300 4.300 0 0 1 12 7.500 4.300 4.300 0 0 1 19.500 10c0 5.400-7.500 10-7.500 10z"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8.500 10.500h.01M12 10.500h.01M15.500 10.500h.01"/>',
  bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.500 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  pencil: '<path d="m4 20 1-4L16 5l3 3L8 19z"/>',
  book: '<path d="M12 6c-2-1.500-5-2-8-1.500v13c3-.500 6 0 8 1.500 2-1.500 5-2 8-1.500v-13c-3-.500-6 0-8 1.500zM12 6v13"/>',
  layers: '<path d="m12 4 9 4.500-9 4.500-9-4.500z"/><path d="m3 12.500 9 4.500 9-4.500M3 16l9 4.500 9-4.500"/>',
  cloud: '<path d="M7 18a4 4 0 0 1-.500-8A5.500 5.500 0 0 1 17 9.500a4.200 4.200 0 0 1 .500 8.500z"/>',
  check: '<path d="m5 12.500 4.500 4.500L19 7.500"/>',
  doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  film: '<path d="M4 9h16v10H4zM4 9l1-4 15 2-.500 2M9 5.600l1.500 3.400M14 6.300l1.500 2.700"/>',
  camera: '<path d="M4 8h3l2-2.500h6L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.500"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  undo: '<path d="M8 7 4 11l4 4"/><path d="M4 11h10a5 5 0 0 1 0 10h-3"/>',
  locate: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  warn: '<path d="M12 4 21 20H3z"/><path d="M12 10v5M12 17.500h.01"/>',
  cal: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
  out: '<path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9"/>',
  upload: '<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>',
};
const ic = (n, s = 24, w = 2) => `<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n] || ''}</svg>`;

/* ---------- общие блоки ---------- */
const book = (cover, title, lg) => `<div class="book ${lg ? 'lg' : ''}" aria-hidden="true"><i><b>${esc(title || '')}</b>${cover ? `<img src="${cover}" alt="">` : '<span></span>'}</i></div>`;
/* Подсказка персонажа: робот объясняет автоматические рекомендации, кот помогает с пунктом выдачи. Закрывается. */
function tip(kind, html, key) {
  if (state.hidden.has(key)) return '';
  return `<div class="tip" id="tip-${key}"><img src="${mimg(kind)}" alt=""><div class="bb">${html}<button data-act="hidetip" data-k="${key}" aria-label="Скрыть подсказку">×</button></div></div>`;
}
function head(title, { back, backAct, right = '' } = {}) {
  state.back = back || '/'; state.backFn = null;
  const b = backAct ? `<button class="ib" data-act="${backAct}" aria-label="Назад">${ic('back')}</button>` : back ? `<a class="ib" href="#${back}" aria-label="Назад">${ic('back')}</a>` : '';
  return `<div class="hd">${b}<h1>${esc(title)}</h1>${right}</div>`;
}
function nav(active) {
  const op = state.user && state.user.role === 'operator';
  const items = op
    ? [['/op/orders', 'box', 'Заказы'], ['/op/tickets', 'chat', 'Обращения'], ['/op/audit', 'layers', 'Журнал'], ['/op/funnel', 'filters', 'Воронка'], ['/profile', 'user', 'Профиль']]
    : [['/', 'home', 'Главная'], ['/projects', 'image', 'Проекты'], ['/new', 'plus', ''], ['/cart', 'cart', 'Корзина'], ['/profile', 'user', 'Профиль']];
  return `<nav class="nav" aria-label="Основная навигация">${items.map(([p, i, t]) => (i === 'plus'
    ? `<a href="#${p}" class="plus" aria-label="Создать проект"><i>${ic('plus', 28, 2.4)}</i></a>`
    : `<a href="#${p}" class="${active === p ? 'on' : ''}">${ic(i, 26, active === p ? 2.4 : 2)}<span>${t}</span></a>`)).join('')}</nav>`;
}
function shell(inner, { active, noNav, hasBack } = {}) {
  const showNav = !noNav && state.user;
  const app = $('#app');
  app.className = showNav ? '' : 'nonav';
  app.innerHTML = `<main>${inner}</main>${showNav ? nav(active) : ''}`;
  platform.setBack(hasBack);
  window.scrollTo(0, 0);
}
function sheet(html, bind, onClose) {
  closeSheet();
  const bg = document.createElement('div');
  bg.className = 'sheet-bg';
  bg.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><button class="sheet-x" data-act="closesheet" aria-label="Закрыть">${ic('close')}</button>${html}</div>`;
  bg.addEventListener('click', (e) => { if (e.target === bg) closeSheet(); });
  bg._onClose = onClose;
  document.body.appendChild(bg);
  if (bind) bind(bg.firstChild);
}
function closeSheet(silent) { const s = $('.sheet-bg'); if (!s) return; const f = s._onClose; s.remove(); if (f && !silent) f(); }
function failScreen(e) {
  if (e instanceof ApiErr && e.status === 401) return go('/login');
  const net = e instanceof NetErr;
  const nf = e instanceof ApiErr && e.status === 404;
  shell(`${head(net ? 'Нет связи' : nf ? 'Не найдено' : 'Что-то пошло не так', { back: '/' })}
  <div class="note ${nf ? 'y' : 'bad'}"><b>${net ? 'Не удалось связаться с сервером.' : esc(e.message || String(e))}</b>${net ? '<br>Черновик и введённые данные сохранены. Проверьте интернет и повторите.' : ''}</div>
  <button class="btn" data-act="reload">Повторить</button><a class="btn line" href="#/">На главную</a>`, { noNav: !state.user });
}

/* Заставка: в v3 — логотип из брендбука, заголовок в две строки без точек и новые персонажи; в v2/классике как раньше. */
function heroHtml() {
  if (V3) return `<section class="hero v3h"><img class="logo" src="img/logo.png" alt="picPac"><h1>Твои фото —<br>твоя история</h1><img class="m-robot" src="${mimg('robot')}" alt=""></section>`;
  return '<section class="hero"><div class="wm">picPac</div><h1>Твои фото.<br>Твоя<br>история.</h1><img class="m-robot" src="img/robot.svg" alt=""><img class="m-cat" src="img/cat.svg" alt=""></section>';
}

/* ---------- вход ---------- */
const LOGIN_RE = /^[A-Za-z0-9_.-]{3,32}$/;
const passwordInput = (id, autocomplete) => `<div class="password-field"><input id="${id}" type="password" autocomplete="${autocomplete}" maxlength="128" required><button type="button" class="password-toggle" data-password="${id}" aria-label="Показать пароль" aria-pressed="false">Показать</button></div>`;
function bindPasswordToggles(root = document) {
  $('[data-password]', root).forEach((btn) => {
    btn.onclick = () => {
      const input = root.querySelector('#' + btn.dataset.password);
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.textContent = show ? 'Скрыть' : 'Показать';
      btn.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
      btn.setAttribute('aria-pressed', String(show));
      input.focus({ preventScroll: true });
    };
  });
}
function loginProblem(login) {
  if (/[А-Яа-яЁё]/.test(login)) return 'Используйте в логине латинские буквы.';
  if (!LOGIN_RE.test(login)) return 'Логин: 3–32 символа — латинские буквы, цифры, точка, дефис или _.';
  return '';
}
async function viewLogin() {
  shell(`${heroHtml()}
  <section class="topsheet"><button class="btn" data-act="guest">Начать без регистрации</button><p class="why">Тестовая версия: деньги не списываются</p>
  <h2>Вход по логину</h2>
  <form id="lf"><label class="lb" for="lg">Логин</label><input id="lg" type="text" autocomplete="username" maxlength="32" autocapitalize="none" autocorrect="off" required>
  <p class="field-hint">Только латинские буквы, цифры, точка, дефис и _</p>
  <label class="lb" for="pw">Пароль</label>${passwordInput('pw', 'current-password')}
  <div id="lerr"></div><p></p><button class="btn line" type="submit">Войти</button></form>
  <p class="muted" style="margin-top:14px">Нет аккаунта? <a href="#" data-act="account" data-mode="register"><u>Создать</u></a></p>
</section>`, { noNav: true });
  bindPasswordToggles($('#lf'));
  $('#lf').onsubmit = async (e) => {
    e.preventDefault();
    const login = $('#lg').value.trim();
    const problem = loginProblem(login);
    if (problem) { $('#lerr').innerHTML = `<div class="note bad">${esc(problem)}</div>`; return; }
    try { await auth('/auth/login', { login, password: $('#pw').value }); } catch (x) { $('#lerr').innerHTML = `<div class="note bad">${esc(x.message)}</div>`; }
  };
}
async function auth(path, body) {
  const r = await api(path, { method: 'POST', body });
  state.token = r.token; state.user = r.user; store.set('pp_token', r.token);
  go(r.user.role === 'operator' ? '/op/orders' : '/');
}

/* Создание аккаунта и вход из окна. У гостя проекты и заказы переходят в аккаунт (merge_token). */
function accountSheet(mode) {
  const reg = mode === 'register';
  const guest = !!(state.user && state.user.guest);
  sheet(`<h2>${reg ? 'Создать аккаунт' : 'Вход в аккаунт'}</h2>${guest ? '<p class="muted">Ваши проекты и заказы из гостевого режима сохранятся в аккаунте.</p>' : ''}
  <label class="lb" for="al">Логин</label><input id="al" type="text" autocomplete="username" maxlength="32" autocapitalize="none" autocorrect="off">
  <p class="field-hint">3–32 символа: латиница, цифры, точка, дефис или _</p>
  <label class="lb" for="ap">Пароль${reg ? ' (не короче 6 символов)' : ''}</label>${passwordInput('ap', reg ? 'new-password' : 'current-password')}
  <div id="aerr"></div><button class="btn" id="ago" style="margin-top:12px">${reg ? 'Создать аккаунт' : 'Войти'}</button>
  <p class="muted" style="margin-top:12px"><a href="#" data-act="account" data-mode="${reg ? 'login' : 'register'}"><u>${reg ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Создать'}</u></a></p>`, (box) => {
    bindPasswordToggles(box);
    const btn = box.querySelector('#ago');
    btn.onclick = async () => {
      const login = box.querySelector('#al').value.trim();
      const password = box.querySelector('#ap').value;
      const problem = loginProblem(login) || (reg && password.length < 6 ? 'Пароль должен содержать не меньше 6 символов.' : '');
      if (problem) { box.querySelector('#aerr').innerHTML = `<div class="note bad">${esc(problem)}</div>`; return; }
      btn.disabled = true;
      try {
        const body = { login, password };
        if (guest) body.merge_token = state.token;
        const r = await api(reg ? '/auth/register' : '/auth/login', { method: 'POST', body });
        state.token = r.token; state.user = r.user; store.set('pp_token', r.token); state.home = null;
        closeSheet(true);
        toast(r.merged_projects ? 'Готово: проекты сохранены в аккаунте' : (reg ? 'Аккаунт создан' : 'Вы вошли'));
        const before = location.hash; go(r.user.role === 'operator' ? '/op/orders' : '/');
        if (location.hash === before) route();
      } catch (e) { box.querySelector('#aerr').innerHTML = `<div class="note bad">${esc(e.message)}</div>`; btn.disabled = false; }
    };
  });
}

/* ---------- главная ---------- */
const chipClass = (s) => (s === 'completed' ? 'ok' : s === 'rejected' ? 'bad' : 'y');
const isActive = (o) => o.status !== 'completed' && o.status !== 'rejected';
async function viewHome() {
  track('app_open');
  const pre = state.home && Date.now() - state.home.t < 20000 ? state.home : null;  // данные пришли вместе со входом
  state.home = null;
  const [projects, orders] = pre ? [pre.projects, pre.orders] : await Promise.all([api('/projects'), api('/orders')]);
  const cont = projects.find((p) => p.status === 'draft');
  const active = orders.find(isActive);
  const pct = cont ? Math.min(100, Math.round((cont.photos_count / cont.min_photos) * 100)) : 0;
  shell(`${heroHtml()}
  <section class="topsheet stack">
    ${V3 ? `<a class="btn big" href="#/new"><span class="g">Создать фотопродукцию<small>Фотокниги, фотографии и другое</small></span>${ic('next', 22)}</a>` : '<a class="btn" href="#/new">Создать фотокнигу</a>'}
    <button class="btn line" data-act="aihome">${ic('spark')}Создать фото с AI</button>
    ${cont ? (V3 ? contCard(cont, pct) : `<a class="card row" href="#/project/${cont.id}/photos">${book(cont.cover, cont.title, true)}<div class="g"><h3 style="font-size:18px">Продолжить проект</h3><div class="muted">${esc(cont.title)} · ${cont.photos_count} фото</div><div class="bar" style="margin-top:12px"><i style="width:${Math.max(6, pct)}%"></i></div></div>${ic('next')}</a>`) : ''}
    ${active ? (V3 ? orderChip(active) : `<a class="rowbtn y" href="#/order/${active.id}" style="min-height:64px">${ic('truck', 32)}<span class="g">${active.status === 'ready' ? 'Фотокнига в пути' : esc(active.status_label)}<small>Заказ ${esc(active.number)}</small></span>${ic('next')}</a>`) : ''}
    <a class="rowbtn" href="#/new?kind=print">${ic('image')}<span class="g">Быстрая печать фото${V3 ? '' : '<small>Без макета, за пару шагов</small>'}</span>${ic('next')}</a>
  </section>`, { active: '/' });
  if (V3) maybeOnboarding();
}

/* Компактные карточки главной (v3): понятный статус и сколько осталось доработать. */
function contCard(cont, pct) {
  const left = Math.max(0, cont.min_photos - cont.photos_count);
  const msg = left ? `Осталось добавить ${left} ${plural(left, 'фото', 'фото', 'фото')}` : 'Можно переходить к макету';
  return `<a class="card cont" href="#/project/${cont.id}/${stepOf(cont.id)}">${book(cont.cover, cont.title)}<div class="g"><div class="ct">Продолжить проект</div>
    <div class="muted">${esc(cont.title)} · ${cont.photos_count} из ${cont.min_photos}</div><div class="bar"><i style="width:${Math.max(6, pct)}%"></i></div><div class="left">${msg}${stepOf(cont.id) !== 'photos' ? ` · этап: ${STEP_NAME[stepOf(cont.id)]}` : ''}</div></div>${ic('next', 20)}</a>`;
}
const STATUS_ICON = { accepted: 'doc', moderation: 'check', production: 'layers', ready: 'truck', completed: 'check' };
function orderChip(o) {
  return `<a class="rowbtn y order" href="#/order/${o.id}"><span class="oi">${ic(STATUS_ICON[o.status] || 'box', 24)}</span><span class="g">${esc(o.status_label)}<small>Заказ ${esc(o.number)}</small></span>${ic('next', 20)}</a>`;
}

/* ---------- знакомство с приложением (v3) ---------- */
const ONB = [
  ['Привет! Я Робо', 'Покажу, как собрать фотокнигу и напечатать фото за пару шагов.'],
  ['Загрузите фото', 'Выберите снимки из галереи. Я проверю качество и подскажу, что поправить.'],
  ['Соберите макет', 'Автомакет разложит фото по страницам, а оформление можно выбрать: детское, путешествие, праздник.'],
  ['Закажите и следите', 'Оформите тестовый заказ и смотрите статус и трек-номер в разделе «Заказы». Нет нужного кадра? Создайте его с AI.'],
];
function maybeOnboarding(force) {
  if (!force && (store.get('pp_onb') === '1' || !state.user || state.user.role === 'operator')) return;
  let k = 0;
  const bg = document.createElement('div');
  bg.className = 'onb';
  const done = () => { store.set('pp_onb', '1'); bg.remove(); };
  const draw = () => {
    const [t, d] = ONB[k];
    bg.innerHTML = `<div class="onb-top"><button class="onb-skip" data-o="skip">Пропустить</button></div>
    <div class="onb-art"><img src="${mimg(['robot', 'cat', 'think', 'happy'][k])}" alt=""></div>
    <div class="onb-card"><h2>${esc(t)}</h2><p>${esc(d)}</p>
    <div class="onb-dots">${ONB.map((_, i) => `<i class="${i === k ? 'on' : ''}"></i>`).join('')}</div>
    <button class="btn" data-o="next">${k === ONB.length - 1 ? 'Начать' : 'Дальше'}</button></div>`;
  };
  bg.addEventListener('click', (e) => {
    const b = e.target.closest('[data-o]'); if (!b) return;
    if (b.dataset.o === 'skip' || k === ONB.length - 1) return done();
    k++; draw();
  });
  draw();
  document.body.appendChild(bg);
}

/* ---------- новый проект ---------- */
async function viewNew() {
  const kind = state.q.get('kind') === 'print' ? 'print' : 'book';
  const products = (await api('/products')).filter((p) => p.kind === kind);
  track('product_view');
  shell(`${head(kind === 'book' ? 'Новая фотокнига' : 'Печать фото', { back: '/' })}
  <div class="seg"><a class="${kind === 'book' ? 'on' : ''}" href="#/new">${ic('book', 20)}Фотокнига</a><a class="${kind === 'print' ? 'on' : ''}" href="#/new?kind=print">${ic('image', 20)}Фото</a><button disabled title="Календари появятся позже">${ic('cal', 20)}Календарь · скоро</button></div>
  <p class="muted">${kind === 'book' ? 'Выберите тип книги. Формат и число страниц можно поменять позже.' : 'Отпечатки с ваших снимков. Формат можно поменять позже.'}</p>
  <div class="stack">${products.map((p) => `<button class="card row" style="width:100%;text-align:left" data-act="create" data-id="${p.id}" data-title="${esc(p.title)}"><span class="g"><h3 style="font-size:18px">${esc(p.title)}</h3><span class="muted">${esc(p.descr)}</span><br><b>от ${byn(Math.min(...p.options.sizes.map((s) => s.base)))}${p.kind === 'print' ? ' за фото' : ''}</b></span>${ic('next')}</button>`).join('')}</div>`, { active: '/new', hasBack: true });
  state.ctx = { create: async (el) => {
    el.disabled = true;
    try {
      const p = await api('/projects', { method: 'POST', body: { product_id: el.dataset.id, title: kind === 'book' ? 'Моя фотокнига' : el.dataset.title } });
      track('project_created'); go(`/project/${p.id}/photos`);
    } catch (e) { el.disabled = false; toast(e.message); }
  } };
}

/* Черновики сверху (с фото раньше пустых), заказанные ниже и приглушены: они уже не требуют действий. */
function projCards(list) {
  const drafts = list.filter((p) => p.status === 'draft').sort((a, b) => (b.photos_count > 0) - (a.photos_count > 0));
  const done = list.filter((p) => p.status !== 'draft');
  const card = (p) => {
    const d = p.status === 'draft';
    const href = d ? `#/project/${p.id}/${stepOf(p.id)}` : p.order_id ? `#/order/${p.order_id}` : '#/orders';
    return `<a class="card row ${d ? '' : 'done'}" href="${href}">${book(p.cover, p.title)}<span class="g"><h3>${esc(p.title)}</h3><span class="muted">${esc(p.product_title)} · ${p.photos_count ? p.photos_count + ' фото' : 'пока без фото'}<br>${fmt(p.updated_at)}</span></span><span class="chip ${d ? 'y' : 'mute'}">${d ? 'Черновик' : 'Заказан'}</span></a>`;
  };
  return `${drafts.length ? `<div class="stack">${drafts.map(card).join('')}</div>` : ''}
  ${done.length ? `<h2 style="margin-top:22px">Заказанные</h2><div class="stack">${done.map(card).join('')}</div>` : ''}`;
}
async function viewProjects() {
  const list = await api('/projects');
  shell(`${head('Мои проекты')}
  ${list.length ? projCards(list) : `${tip('cat', 'Здесь появятся ваши проекты. Черновики сохраняются сами, к ним можно вернуться.', 'noproj')}`}
  <p></p><a class="btn" href="#/new">Создать проект</a>`, { active: '/projects' });
}

/* ---------- параметры продукта ---------- */
function optionsPanel(product, settings, onChange) {
  const k = product.kind === 'book' ? 'cover' : 'finish';
  const o = product.options;
  const btn = (key, id, label, cur) => `<button type="button" class="opt ${cur === id ? 'on' : ''}" data-opt="${key}" data-val="${id}">${esc(label)}</button>`;
  const pg = product.pages;
  const html = `<label class="lb">Формат</label><div class="opts">${o.sizes.map((s) => btn('size', s.id, s.label, settings.size)).join('')}</div>
  <label class="lb">${product.kind === 'book' ? 'Обложка' : 'Поверхность'}</label><div class="opts">${o.covers.map((c) => btn(k, c.id, c.label, settings[k])).join('')}</div>
  ${pg ? `<label class="lb" for="pgs">Страниц: <span id="pgv">${settings.pages}</span></label><input id="pgs" type="range" min="${pg.min}" max="${pg.max}" step="${pg.step}" value="${settings.pages}">` : ''}
  <label class="lb" for="cps">${product.kind === 'book' ? 'Экземпляров' : 'Копий каждого фото'}: <span id="cpv">${settings.copies}</span></label><input id="cps" type="range" min="1" max="10" value="${settings.copies}">`;
  return {
    html,
    bind(root) {
      $$('[data-opt]', root).forEach((b) => b.addEventListener('click', () => { settings[b.dataset.opt] = b.dataset.val; onChange(true); }));
      const pgs = $('#pgs', root); if (pgs) { pgs.oninput = () => { settings.pages = +pgs.value; $('#pgv', root).textContent = pgs.value; onChange(false); }; }
      const cps = $('#cps', root); cps.oninput = () => { settings.copies = +cps.value; $('#cpv', root).textContent = cps.value; onChange(false); };
    },
  };
}
function paramsLabel(project) {
  const s = project.settings, o = project.product.options;
  const size = (o.sizes.find((x) => x.id === s.size) || {}).label || s.size;
  const cov = (o.covers.find((x) => x.id === (s.cover || s.finish)) || {}).label || '';
  return [size, cov, s.pages ? `${s.pages} стр.` : '', `${s.copies} шт.`].filter(Boolean).join(' · ');
}
function paramsSheet(project, after) {
  const settings = { ...project.settings };
  let t, changed = false;
  const price = async () => { try { const r = await api('/price', { method: 'POST', body: { product_id: project.product.id, settings, project_id: project.id } }); const el = $('#ppr'); if (el) el.textContent = byn(r.subtotal); } catch { /* цена обновится позже */ } };
  const save = () => {
    changed = true; clearTimeout(t);
    const st = $('#pst'); if (st) st.textContent = 'Сохраняем…';
    t = setTimeout(async () => {
      try { await api('/projects/' + project.id, { method: 'PATCH', body: { settings } }); await price(); const s2 = $('#pst'); if (s2) s2.textContent = 'Сохранено'; }
      catch (e) { const s2 = $('#pst'); if (s2) s2.innerHTML = `<span style="color:var(--bad)">Не удалось сохранить: ${esc(e.message)}</span>`; }
    }, 300);
  };
  sheet(`<h2>Параметры</h2><div id="po"></div>${project.product.kind === 'book' ? '<p></p><button class="btn sm grey" data-act="suggestpages">Подобрать число страниц по числу фото</button>' : ''}
  <div class="total"><span>${project.product.kind === 'print' ? 'Стоимость печати' : 'Цена без доставки'}</span><span id="ppr">…</span></div><p class="muted" id="pst">Изменения сохраняются автоматически</p><button class="btn" data-act="closesheet">Готово</button>`, (root) => {
    const draw = () => { const p = optionsPanel(project.product, settings, (re) => { save(); if (re) draw(); }); $('#po', root).innerHTML = p.html; p.bind($('#po', root)); };
    draw(); price();
    state.ctx.suggestpages = async () => {
      try { const s = await api(`/projects/${project.id}/suggest-pages`); settings.pages = s.pages; draw(); save(); toast(`Подобрано страниц: ${s.pages}`); } catch (e) { toast(e.message); }
    };
  }, () => { clearTimeout(t); if (changed && after) setTimeout(after, 350); });
}

/* ---------- проект ---------- */
async function viewProject(id, step) {
  const wantCheck = ['photos', 'check', 'cart'].includes(step === 'delivery' || step === 'order' ? 'cart' : step);
  // проект и проверка запрашиваются одновременно: через туннель каждый круг запрос-ответ стоит секунды
  const [project, pre] = await Promise.all([api('/projects/' + id), wantCheck ? api(`/projects/${id}/check`).catch(() => null) : null]);
  state.preChk = pre;
  if (project.status !== 'draft') return go('/orders');
  if (step === 'delivery' || step === 'order') step = 'cart';
  const fn = { photos: stepPhotos, edit: stepEdit, layout: stepLayout, check: stepCheck, cart: stepCart, ai: stepAI }[step];
  if (!fn || (step === 'layout' && project.product.kind !== 'book')) return go(`/project/${id}/photos`);
  if (step !== 'ai') store.set('pp_step_' + id, step);  // «Продолжить» откроет этот этап
  await fn(project);
}
const stepOf = (id) => { const k = store.get('pp_step_' + id); return ['photos', 'edit', 'layout', 'check', 'cart'].includes(k) ? k : 'photos'; };
const STEP_NAME = { photos: 'фото', edit: 'правки', layout: 'макет', check: 'проверка', cart: 'оформление' };
const isBook = (p) => p.product.kind === 'book';

/* Чистка подборки: убрать дубликаты (остаётся лучший кадр серии) и неудачные снимки. */
const cleanNums = (chk) => ({ dup: (chk.duplicates || []).reduce((s, g) => s + g.remove.length, 0), bad: (chk.bad || []).length });
function cleanCard(project, chk, link) {
  const { dup, bad } = cleanNums(chk);
  if (!dup && !bad) return '';
  const badWhy = [...new Set((chk.bad || []).map((b) => b.reason))].join(', ');
  return `<div class="note y"><b>Можно почистить подборку</b>
    <div class="muted" style="margin:4px 0 10px">${dup ? `Похожих кадров: ${dup}. Из каждой серии оставим самый чёткий и крупный. ` : ''}${bad ? `Неудачных снимков: ${bad} (${esc(badWhy)}). ` : ''}Удалённое вернуть нельзя, но оригиналы на вашем телефоне остаются.</div>
    <div class="acts">${dup ? `<button class="btn sm" data-act="clean" data-kind="duplicates">Убрать дубликаты (${dup})</button>` : ''}${bad ? `<button class="btn sm ${dup ? 'line' : ''}" data-act="clean" data-kind="bad">Убрать неудачные (${bad})</button>` : ''}${link ? `<a class="btn sm grey" href="${link}">Подробнее</a>` : ''}</div></div>`;
}
async function doCleanup(project, kind, after) {
  const body = { duplicates: kind === 'duplicates', bad: kind === 'bad' };
  try {
    const r = await api(`/projects/${project.id}/cleanup`, { method: 'POST', body });
    toast(r.removed ? `Убрано фото: ${r.removed}` : 'Нечего убирать');
    await after();
  } catch (x) { toast(x.message); }
}
const acked = (p) => store.get('pp_ack_' + p.id) === '1';
const WARN_SHORT = { low_resolution: 'мало пикселей', blurry: 'смазано', too_dark: 'темно', duplicate: 'похожее', overexposed: 'пересвет', crop_risk: 'обрежется' };

/* ---------- выбор фото ---------- */
/* Перед отправкой уменьшаем большие фото до 3600 px по длинной стороне: в печать это не влияет (хватает для A3), зато
   загрузка через мобильную сеть и туннель в разы быстрее. EXIF и геометки при этом тоже пропадают. HEIC и сбои декодирования отправляем как есть. */
async function shrink(file) {
  if (file.size < 900 * 1024 || !/^image\/(jpeg|png|webp)$/.test(file.type) || !window.createImageBitmap) return file;
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 3600 / Math.max(bmp.width, bmp.height));
    if (k === 1 && file.size < 3 * 1024 * 1024) { bmp.close(); return file; }
    const c = document.createElement('canvas'); c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.drawImage(bmp, 0, 0, c.width, c.height); bmp.close();
    const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.9));
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
  } catch { return file; }
}
const takeCheck = async (project) => { const c = state.preChk; state.preChk = null; return c || api(`/projects/${project.id}/check`); };
async function stepPhotos(project) {
  const chk = await takeCheck(project);
  const warns = {};
  chk.warnings.forEach((w) => { if (w.photo_id) (warns[w.photo_id] ||= []).push(w.code); });
  const inc = project.photos.filter((p) => !p.excluded), exc = project.photos.filter((p) => p.excluded);
  const n = inc.length, tot = project.photos.length, min = project.product.min_photos, need = Math.max(0, min - n);
  const editMode = state.q.get('m') === 'edit';
  /* Плитка фото. Smart Story: ★ закрепляет кадр (своя страница), ⊘ убирает из книги без удаления, «Вернуть» возвращает. */
  const tile = (p) => {
    const i = project.photos.indexOf(p);
    return `<div class="tile${p.excluded ? ' off' : ''}"><a href="#/project/${project.id}/edit?i=${i}" aria-label="Открыть фото ${i + 1} в редакторе"><img src="${p.thumb}" alt="Фото ${i + 1}" loading="lazy"></a><span class="n">${i + 1}</span>${p.pinned ? '<span class="pin">★ главное</span>' : ''}${warns[p.id] ? `<span class="w">${ic('warn', 13, 2.4)}${WARN_SHORT[warns[p.id][0]] || 'проверьте'}</span>` : ''}${!editMode && !p.pinned && Object.keys(p.edits || {}).length ? '<span class="e">с правками</span>' : ''}${p.excluded ? `<button class="back" data-act="flag" data-id="${p.id}" data-k="excluded" data-v="0">Вернуть в книгу</button>` : editMode ? `<div class="tl2"><button data-act="flag" data-id="${p.id}" data-k="pinned" data-v="${p.pinned ? 0 : 1}" class="${p.pinned ? 'on' : ''}" aria-label="${p.pinned ? 'Открепить кадр' : 'Закрепить кадр: своя страница'}">${p.pinned ? '★ главное' : '☆'}</button><button data-act="flag" data-id="${p.id}" data-k="excluded" data-v="1" aria-label="Убрать из книги, фото не удаляется">⊘</button></div><div class="tl"><button data-act="mv" data-id="${p.id}" data-d="-1" aria-label="Передвинуть раньше">‹</button><button data-act="rm" data-id="${p.id}" aria-label="Удалить фото навсегда">✕</button><button data-act="mv" data-id="${p.id}" data-d="1" aria-label="Передвинуть позже">›</button></div>` : ''}</div>`;
  };
  const dups = chk.warnings.filter((w) => w.code === 'duplicate').length;
  const bad = new Set(chk.warnings.filter((w) => w.photo_id && w.code !== 'duplicate').map((w) => w.photo_id)).size;
  shell(`${head(project.title, { back: '/', right: `<button class="pillbtn" data-act="next" ${need ? 'disabled' : ''}>Далее</button>` })}
  <button class="rowbtn" data-act="params">${ic(isBook(project) ? 'book' : 'image')}<span class="g">${esc(project.product.title)}<small>${esc(paramsLabel(project))}</small></span>${ic('next')}</button>
  ${n ? `<div class="lead" style="margin-top:12px"><img src="${inc[0].proxy || inc[0].url}" alt="Первое фото проекта"></div>` : ''}
  <div id="up"></div>
  ${n ? `<div class="row" style="margin:14px 0 10px"><button class="chip g" style="border:0;justify-content:flex-start;background:none;font-size:15px;padding-left:0" data-act="toggleedit">${editMode ? 'Готово' : 'Галерея · изменить порядок'}</button><span class="chip y" style="font-size:14px;padding:7px 13px">Выбрано ${n}</span></div>` : ''}
  ${n ? cleanCard(project, chk, `#/project/${project.id}/check`) : ''}
  ${n && !cleanNums(chk).dup && !cleanNums(chk).bad && (dups || bad) ? `<a class="note y" style="display:block" href="#/project/${project.id}/check"><b>Проверка фото:</b> ${bad} ${plural(bad, 'фото', 'фото', 'фото')} требуют внимания. Посмотреть ›</a>` : ''}
  ${editMode ? '<p class="why" style="margin:0 0 8px">★ закрепить кадр: он получит отдельную страницу. ⊘ убрать из книги: фото останется в проекте, его можно вернуть. ✕ удалить навсегда.</p>' : ''}
  ${tot ? `${exc.length ? `<h2 style="font-size:18px;margin:6px 0 8px">В книге · ${n}</h2>` : ''}<div class="grid${editMode ? ' edit' : ''}">${inc.map(tile).join('')}
    <div class="tile add"><label for="file">${ic('plus', 26)}Добавить</label></div></div>
    ${exc.length ? `<h2 style="font-size:18px;margin:18px 0 4px">Остальные · ${exc.length}</h2><p class="why" style="margin:0 0 8px">Не в книге. Файлы сохранены, вернуть можно в один тап.</p><div class="grid">${exc.map(tile).join('')}</div>` : ''}`
    : `${tip('cat', `Добавьте фотографии из галереи: минимум ${min}. Подойдут JPEG, PNG, HEIC с iPhone, WEBP, TIFF и BMP, до 15 МБ каждое.`, 'nophotos')}<label class="btn" for="file" style="cursor:pointer">${ic('upload')}Добавить фотографии</label>`}
  <input id="file" class="hide" type="file" multiple accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif,image/webp,image/tiff,image/bmp">
  <p></p><a class="btn line" href="#/project/${project.id}/ai?mode=create">${ic('spark')}Создать фото с AI</a>
  <div class="foot">${ic('cloud', 22)}Сохраняем автоматически</div>
  ${n ? `<button class="btn" data-act="next" ${need ? 'disabled' : ''}>Продолжить с фото</button>${need ? `<p class="why">Добавьте ещё ${need} фото: для этого продукта нужно минимум ${min}</p>` : ''}` : ''}`, { active: '/projects', hasBack: true });
  $('#file').onchange = async (e) => {
    const files = [...e.target.files]; if (!files.length) return;
    const box = $('#up'); let rejected = [], okCount = 0, doneN = 0;
    const show = () => { box.innerHTML = `<div class="note y"><span class="spin"></span>Загружаем ${Math.min(doneN + 1, files.length)} из ${files.length}…<div class="bar" style="margin-top:10px"><i style="width:${(doneN / files.length) * 100}%"></i></div></div>`; };
    show();
    let next = 0;
    const worker = async () => {  // три загрузки одновременно: через туннель одна за другой слишком долго
      while (next < files.length) {
        const f = files[next++];
        try {
          const small = await shrink(f);
          const fd = new FormData(); fd.append('files', small, small.name);
          const r = await api(`/projects/${project.id}/photos`, { method: 'POST', form: fd });
          rejected = rejected.concat(r.rejected); okCount += r.added || 0;
        } catch (x) { rejected.push({ name: f.name, reason: x instanceof NetErr ? 'нет связи' : x.message }); }
        doneN++; show();
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    track('photos_uploaded');
    await viewProject(project.id, 'photos');
    if (rejected.length) $('#up').innerHTML = `<div class="note bad"><b>Не удалось загрузить ${rejected.length} фото. Остальные сохранены${okCount ? ` (${okCount})` : ''}.</b><br>${rejected.map((r) => `${esc(r.name)}: ${esc(r.reason)}`).join('<br>')}<label class="btn sm line" for="file" style="cursor:pointer">Повторить загрузку</label></div>`;
  };
  const again = () => viewProject(project.id, 'photos');
  state.ctx = {
    next: () => go(`/project/${project.id}/edit`),
    clean: (el) => doCleanup(project, el.dataset.kind, again),
    params: () => paramsSheet(project, again),
    toggleedit: () => go(`/project/${project.id}/photos${editMode ? '' : '?m=edit'}`),
    flag: async (el) => {
      try { await api(`/projects/${project.id}/photos/${el.dataset.id}/flags`, { method: 'PATCH', body: { [el.dataset.k]: el.dataset.v === '1' } }); toast(el.dataset.k === 'pinned' ? (el.dataset.v === '1' ? 'Кадр закреплён: получит свою страницу' : 'Кадр откреплён') : (el.dataset.v === '1' ? 'Убрано из книги, файл сохранён' : 'Фото вернулось в книгу')); await again(); } catch (x) { toast(x.message); }
    },
    rm: async (el) => { try { await api(`/projects/${project.id}/photos/${el.dataset.id}`, { method: 'DELETE' }); await again(); } catch (x) { toast(x.message); } },
    mv: async (el) => {
      const ids = project.photos.map((x) => x.id), i = ids.indexOf(el.dataset.id), j = i + +el.dataset.d;
      if (j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      try { await api('/projects/' + project.id, { method: 'PATCH', body: { photo_order: ids } }); await again(); } catch (x) { toast(x.message); }
    },
  };
}

/* Нейросетевое улучшение качества (Real-ESRGAN, на нашем сервере): увеличивает число пикселей и добавляет чёткость. */
async function doUpscale(project, photoId, scale, el) {
  const label = el ? el.innerHTML : '';
  if (el) { el.disabled = true; el.innerHTML = '<span class="spin"></span>Улучшаем…'; }
  try {
    const r = await api(`/projects/${project.id}/photos/${photoId}/upscale`, { method: 'POST', body: { scale } });
    const ph = r.photos.find((x) => x.id === photoId);
    toast(`Готово: теперь ${ph.width}×${ph.height} px. Оригинал сохранён`);
    return r;
  } catch (x) {
    toast(x.message);
    if (el) { el.disabled = false; el.innerHTML = label; }
    return null;
  }
}

/* ---------- редактор ---------- */
async function stepEdit(project) {
  if (!project.photos.length) return go(`/project/${project.id}/photos`);
  const idx = Math.max(0, Math.min(project.photos.length - 1, +(state.q.get('i') || 0)));
  let ph = project.photos[idx];
  let edits = clone(ph.edits || {});
  let hist = [], snap = JSON.stringify(edits), tab = state.q.get('tab') || 'filters', dirty = false, objUrl = null, timer = null, seq = 0;
  const base = () => { const b = clone(edits); delete b.filter; delete b.text; delete b.adjust; return b; };
  shell(`<div class="stage"><img id="stage" alt="Фото в редакторе" src="${ph.proxy || ph.url}">
    <button class="fab bk" data-act="back" aria-label="Назад к фотографиям">${ic('back')}</button>
    <div class="fabs"><button class="fab" data-tab="crop" aria-label="Кадр">${ic('crop')}</button><button class="fab" data-tab="text" aria-label="Текст">${ic('text')}</button><button class="fab" data-tab="ai" aria-label="Улучшение">${ic('spark')}</button></div>
    <span class="busy hide" id="busy">Обновляем…</span></div>
  <div class="strip" id="strip">${project.photos.map((p, i) => `<button class="${i === idx ? 'on' : ''}" data-i="${i}" aria-label="Фото ${i + 1}"><img src="${p.thumb}" alt=""></button>`).join('')}</div>
  <div class="mini"><button data-act="undo">${ic('undo', 18)}Отменить</button><button data-act="reset">Сбросить</button><button id="cmp" aria-label="Удерживайте, чтобы увидеть оригинал">Держите: до</button></div>
  <div class="panel" id="panel"></div>
  <div class="dock"><div class="tabs4" id="tabs"><button data-tab="filters">${ic('filters')}Фильтры</button><button data-tab="text">${ic('text')}Текст</button><button data-tab="crop">${ic('frame')}Кадр</button><button data-tab="ai">${ic('spark')}AI</button></div>
  <button class="btn" data-act="next">${isBook(project) ? 'Собрать альбом' : 'Проверить фото'}</button>
  <p class="why" id="savest">Оригинал не меняется: любую правку можно отменить</p></div>`, { noNav: true, hasBack: true });

  let baseKey = '', baseUrl = null;
  const ensureBase = async () => {  // кадр без фильтра и яркости: на нём CSS показывает правки мгновенно
    const key = `${ph.id}:${ph.version}:${JSON.stringify(base())}`;
    if (key === baseKey) return;
    baseKey = key; baseUrl = null;
    try { const u = await apiBlob(`/projects/${project.id}/photos/${ph.id}/preview`, { edits: base(), size: 900 }); if (key === baseKey) baseUrl = u; } catch { /* без мгновенного предпросмотра */ }
  };
  const instant = () => {
    const st = $('#stage'); if (!st || !baseUrl || edits.text) return;
    if (st.dataset.base !== baseUrl) { st.src = baseUrl; st.dataset.base = baseUrl; }
    st.style.filter = cssOf(edits);
  };
  const refresh = (fast) => {
    if (fast) instant();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq; $('#busy').classList.remove('hide');
      try {
        const url = await apiBlob(`/projects/${project.id}/photos/${ph.id}/preview`, { edits, size: 900 });
        if (mine !== seq) { URL.revokeObjectURL(url); return; }
        const old = objUrl; objUrl = url; const st = $('#stage'); if (st) { st.src = url; st.style.filter = ''; st.dataset.base = ''; } if (old) URL.revokeObjectURL(old);
        ensureBase();
      } catch (x) { toast(x.message); } finally { const b = $('#busy'); if (b && mine === seq) b.classList.add('hide'); }
    }, fast ? 500 : 160);
  };
  const commit = () => { const now = JSON.stringify(edits); if (now !== snap) { hist.push(snap); if (hist.length > 40) hist.shift(); snap = now; dirty = true; const s = $('#savest'); if (s) s.textContent = 'Есть несохранённые правки: сохраним при переходе'; } };
  const setTab = (t) => { tab = t; $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === t)); panel(); };
  const chips = (items, cur, attr) => `<div class="opts">${items.map(([v, l]) => `<button type="button" class="opt ${cur === v ? 'on' : ''}" data-${attr}="${v}">${l}</button>`).join('')}</div>`;
  const slider = (label, attr, key, min, max, val, step = 1) => `<div class="ctl"><span>${label}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${val}" data-${attr}="${key}" aria-label="${label}"></div>`;

  function panel() {
    const box = $('#panel');
    if (tab === 'filters') {
      const a = edits.adjust || {};
      box.innerHTML = `<div class="fcs">${FILTERS.map((f) => `<button class="fc ${(edits.filter || 'none') === f ? 'on' : ''}" data-f="${f}"><i><img data-fimg="${f}" alt=""></i>${FILTER_LABELS[f]}</button>`).join('')}</div>
      ${slider('Яркость', 'adj', 'brightness', -100, 100, a.brightness || 0)}${slider('Контраст', 'adj', 'contrast', -100, 100, a.contrast || 0)}${slider('Насыщенность', 'adj', 'saturation', -100, 100, a.saturation || 0)}`;
      FILTERS.forEach((f) => { const im = box.querySelector(`[data-fimg="${f}"]`); if (im) { im.src = ph.thumb; im.style.filter = FILTER_CSS[f]; } });  // кружки без запросов к серверу
      ensureBase();
      $$('[data-f]', box).forEach((b) => (b.onclick = () => { if (b.dataset.f === 'none') delete edits.filter; else edits.filter = b.dataset.f; commit(); refresh(true); $$('[data-f]', box).forEach((x) => x.classList.toggle('on', x === b)); }));
      $$('[data-adj]', box).forEach((r) => {
        r.oninput = () => { edits.adjust = { ...(edits.adjust || {}), [r.dataset.adj]: +r.value }; refresh(true); };
        r.onchange = commit;
      });
    } else if (tab === 'text') {
      const def = { value: '', pos: 'bottom', color: 'white', size: 7 };
      const t = edits.text || def;
      box.innerHTML = `<label class="lb" for="tv">Подпись на фото</label><input id="tv" type="text" maxlength="60" placeholder="Например: Наше лето" value="${esc(t.value)}">
      <label class="lb">Положение</label>${chips([['top', 'Сверху'], ['center', 'По центру'], ['bottom', 'Снизу']], t.pos, 'tp')}
      <label class="lb">Цвет</label>${chips([['white', 'Белый'], ['black', 'Чёрный'], ['yellow', 'Жёлтый']], t.color, 'tc')}
      ${slider('Размер', 'tsz', 'size', 3, 14, t.size)}`;
      const upd = (patch) => { const cur = { ...(edits.text || def), ...patch }; if (cur.value.trim()) edits.text = cur; else delete edits.text; };
      $('#tv', box).oninput = (e) => { upd({ value: e.target.value }); refresh(); };
      $('#tv', box).onchange = commit;
      $$('[data-tp]', box).forEach((b) => (b.onclick = () => { upd({ pos: b.dataset.tp }); commit(); refresh(); panel(); }));
      $$('[data-tc]', box).forEach((b) => (b.onclick = () => { upd({ color: b.dataset.tc }); commit(); refresh(); panel(); }));
      box.querySelector('[data-tsz]').oninput = (e) => { upd({ size: +e.target.value }); refresh(); };
      box.querySelector('[data-tsz]').onchange = commit;
    } else if (tab === 'crop') {
      const def = { ratio: 'orig', zoom: 1, x: 0.5, y: 0.5 };
      const c = edits.crop || def;
      box.innerHTML = `<label class="lb">Пропорции кадра</label>${chips([['orig', 'Оригинал'], ['1:1', '1:1'], ['4:5', '4:5'], ['3:2', '3:2'], ['2:3', '2:3'], ['16:9', '16:9']], c.ratio, 'cr')}
      ${slider('Масштаб', 'cz', 'zoom', 1, 4, c.zoom, 0.05)}${slider('Влево-вправо', 'cx', 'x', 0, 1, c.x, 0.02)}${slider('Вверх-вниз', 'cy', 'y', 0, 1, c.y, 0.02)}
      <div class="opts" style="margin-top:8px"><button class="opt" data-rot="270">⟲ Влево</button><button class="opt" data-rot="90">⟳ Вправо</button><button class="opt ${edits.flip ? 'on' : ''}" data-flip="1">⇋ Зеркало</button></div>
      <p class="muted">На экране видно ровно ту часть, что останется. Если после кадрирования пикселей станет мало для печати, предупредим на проверке.</p>`;
      const upd = (patch) => { const cur = { ...(edits.crop || def), ...patch }; if (cur.ratio === 'orig' && cur.zoom <= 1) delete edits.crop; else edits.crop = cur; };
      $$('[data-cr]', box).forEach((b) => (b.onclick = () => { upd({ ratio: b.dataset.cr }); commit(); refresh(); panel(); }));
      [['cz', 'zoom'], ['cx', 'x'], ['cy', 'y']].forEach(([a, k]) => { const r = box.querySelector(`[data-${a}]`); r.oninput = () => { upd({ [k]: +r.value }); refresh(); }; r.onchange = commit; });
      $$('[data-rot]', box).forEach((b) => (b.onclick = () => { const r = ((edits.rotate || 0) + +b.dataset.rot) % 360; if (r) edits.rotate = r; else delete edits.rotate; commit(); refresh(); }));
      box.querySelector('[data-flip]').onclick = () => { if (edits.flip) delete edits.flip; else edits.flip = true; commit(); refresh(); panel(); };
    } else {
      const bg = edits.bg ? edits.bg.mode : 'off';
      box.innerHTML = `<div class="card" style="margin-top:10px"><b>Улучшить качество</b>${state.caps.upscale ? `<p class="muted">Нейросеть добавит чёткости и увеличит фото: сейчас ${ph.width}×${ph.height} px. Обработка на нашем сервере, фото никуда не передаётся. Занимает несколько секунд.</p>${ph.retouched ? '<div class="note y">После удаления объектов увеличить фото нельзя. Сначала верните исходное в блоке «Убрать объект».</div>' : ph.upscaled ? '<div class="note ok">Улучшение применено.</div><div id="upcmp"><span class="muted">Готовим сравнение…</span></div><button class="btn sm line" data-act="unup">Вернуть исходное качество</button>' : '<div class="two"><button class="btn sm" data-act="up2">Увеличить ×2</button><button class="btn sm line" data-act="up4">Увеличить ×4</button></div>'}` : '<p class="muted">В этой сборке недоступно: модель не установлена (см. README).</p>'}</div>
      <div class="card" style="margin-top:10px"><div class="row"><span class="g"><b>Автоулучшение</b><br><span class="muted">Выравнивает свет и контраст, добавляет резкость. Обычная обработка, не нейросеть.</span></span><button class="btn sm ${edits.auto ? '' : 'line'}" data-auto="1">${edits.auto ? '✓ Вкл.' : 'Включить'}</button></div></div>
      <div class="card" style="margin-top:10px"><b>Убрать объект</b>${state.caps.object_removal ? `<p class="muted">Закрасьте лишнее: прохожего, мусор, провод. Нейросеть дорисует фон. Обработка на нашем сервере, фото никуда не передаётся. Занимает несколько секунд.</p>${ph.retouched ? '<div class="note ok">Объекты убраны.</div><div class="two"><button class="btn sm" data-act="erase">Убрать ещё</button><button class="btn sm line" data-act="unerase">Вернуть как было</button></div>' : '<button class="btn sm" data-act="erase">Закрасить и убрать</button>'}` : '<p class="muted">В этой сборке недоступно: модель не установлена (см. README).</p>'}</div>
      <div class="card" style="margin-top:10px"><b>Убрать фон</b>${state.caps.bg_removal ? `<p class="muted">Нейросеть вырезает объект прямо на нашем сервере, фото никуда не передаётся.</p>${chips([['off', 'Выкл.'], ['white', 'Белый'], ['yellow', 'Жёлтый'], ['black', 'Чёрный'], ['blur', 'Размытие']], bg, 'bg')}<div id="bgmsg"></div>` : '<p class="muted">В этой сборке недоступно: модель не установлена (см. README).</p>'}</div>
      <a class="btn line" style="margin-top:10px" data-act="tostudio">${ic('spark')}AI-студия: создать новый кадр</a>`;
      if (ph.upscaled && !ph.retouched && box.querySelector('#upcmp')) {
        api(`/projects/${project.id}/photos/${ph.id}/upscale-compare`).then((c) => {
          const el = box.querySelector('#upcmp'); if (!el) return;
          el.innerHTML = `<p class="muted" style="margin:8px 0 6px">Один и тот же участок в натуральном масштабе: было ${c.w0}×${c.h0}, стало ${c.w1}×${c.h1} px. На экране целиком разница почти не видна, на печати заметна.</p><div class="two"><figure style="margin:0"><img src="${c.before}" alt="До" style="width:100%;border-radius:10px"><figcaption class="muted" style="text-align:center">До</figcaption></figure><figure style="margin:0"><img src="${c.after}" alt="После" style="width:100%;border-radius:10px"><figcaption class="muted" style="text-align:center">После</figcaption></figure></div>`;
        }).catch(() => { const el = box.querySelector('#upcmp'); if (el) el.innerHTML = ''; });
      }
      box.querySelector('[data-auto]').onclick = () => { if (edits.auto) delete edits.auto; else edits.auto = true; commit(); refresh(); panel(); };
      $$('[data-bg]', box).forEach((b) => (b.onclick = async () => {
        const m = b.dataset.bg;
        if (m === 'off') { delete edits.bg; commit(); refresh(); return panel(); }
        if (!ph.has_mask) {
          $('#bgmsg').innerHTML = '<p class="muted"><span class="spin"></span>Вырезаем объект…</p>';
          try { const p = await api(`/projects/${project.id}/photos/${ph.id}/bg-mask`, { method: 'POST' }); project.photos = p.photos; ph = p.photos[idx]; }
          catch (x) { $('#bgmsg').innerHTML = `<div class="note bad">${esc(x.message)}</div>`; return; }
        }
        edits.bg = { mode: m }; commit(); refresh(); panel();
      }));
    }
  }
  async function save() {
    clearTimeout(timer);
    if (!dirty) return true;
    const s = $('#savest'); if (s) s.textContent = 'Сохраняем…';
    try {
      const p = await api(`/projects/${project.id}/photos/${ph.id}/edit`, { method: 'PUT', body: { edits } });
      project.photos = p.photos; ph = p.photos[idx]; dirty = false; return true;
    } catch (x) {
      const s2 = $('#savest'); if (s2) s2.innerHTML = `<span style="color:var(--bad)">Не удалось сохранить последние изменения: ${esc(x.message)}. Нажмите ещё раз, чтобы повторить.</span>`;
      return false;
    }
  }
  const leave = async (path) => { if (await save()) go(path); };
  $$('#tabs button, .fabs .fab').forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
  $$('#strip button').forEach((b) => (b.onclick = () => { if (+b.dataset.i !== idx) leave(`/project/${project.id}/edit?i=${b.dataset.i}&tab=${tab}`); }));
  const cmp = $('#cmp'), show = (u) => { const st = $('#stage'); if (st) st.src = u; };
  cmp.onpointerdown = (e) => { e.preventDefault(); show(ph.orig); };
  ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => (cmp['on' + ev] = () => show(objUrl || ph.proxy || ph.url)));
  setTab(tab);
  const sel = $('#strip .on'); if (sel && sel.scrollIntoView) sel.scrollIntoView({ inline: 'center', block: 'nearest' });
  state.backFn = () => leave(`/project/${project.id}/photos`);
  state.ctx = {
    back: state.backFn,
    undo: () => { if (!hist.length) return toast('Нечего отменять'); edits = JSON.parse(hist.pop()); snap = JSON.stringify(edits); dirty = true; refresh(); panel(); },
    reset: () => { if (!Object.keys(edits).length) return toast('Правок нет'); edits = {}; commit(); refresh(); panel(); toast('Правки сброшены. Вернуть: «Отменить»'); },
    erase: async () => { if (await save()) eraseSheet(project, ph, async () => { await route(); }); },
    unerase: async (el) => { el.disabled = true; try { await api(`/projects/${project.id}/photos/${ph.id}/erase`, { method: 'DELETE' }); toast('Вернули фото как было'); await route(); } catch (x) { el.disabled = false; toast(x.message); } },
    tostudio: () => leave(`/project/${project.id}/ai?mode=create`),
    up2: async (el) => { if (!(await save())) return; if (await doUpscale(project, ph.id, 2, el)) go(`/project/${project.id}/edit?i=${idx}&tab=ai`); },
    up4: async (el) => { if (!(await save())) return; if (await doUpscale(project, ph.id, 4, el)) go(`/project/${project.id}/edit?i=${idx}&tab=ai`); },
    unup: async (el) => { el.disabled = true; try { await api(`/projects/${project.id}/photos/${ph.id}/upscale`, { method: 'DELETE' }); toast('Вернули исходное качество'); go(`/project/${project.id}/edit?i=${idx}&tab=ai`); } catch (x) { el.disabled = false; toast(x.message); } },
    next: () => leave(`/project/${project.id}/${isBook(project) ? 'layout' : 'check'}`),
  };
}

/* Удаление объекта: закрашиваем кистью поверх фото без правок (чтобы координаты совпали с исходником), сервер дорисовывает фон. */
async function eraseSheet(project, ph, done) {
  let url;
  try { url = await apiBlob(`/projects/${project.id}/photos/${ph.id}/preview`, { edits: {}, size: 900 }); } catch (x) { return toast(x.message); }
  sheet(`<h2>Убрать объект</h2><p class="muted" style="margin-top:0">Закрасьте то, что нужно убрать. Лучше закрашивать чуть шире объекта, вместе с тенью.</p>
    <div class="paint"><img id="pimg" src="${url}" alt="Фото для закрашивания"><canvas id="pcv"></canvas></div>
    <div class="ctl"><span>Кисть</span><input id="pbr" type="range" min="6" max="90" value="34" aria-label="Размер кисти"></div>
    <div class="two"><button class="btn sm line" id="pundo">Отменить мазок</button><button class="btn sm line" id="pclr">Стереть всё</button></div>
    <button class="btn" id="pgo" style="margin-top:12px">Убрать закрашенное</button><div id="perr"></div>`, (root) => {
    const img = $('#pimg', root), cv = $('#pcv', root), br = $('#pbr', root);
    const strokes = []; let cur = null;
    const fit = () => { cv.width = img.naturalWidth; cv.height = img.naturalHeight; redraw(); };
    const redraw = () => {
      const g = cv.getContext('2d'); g.clearRect(0, 0, cv.width, cv.height);
      g.lineCap = 'round'; g.lineJoin = 'round'; g.strokeStyle = '#ff2d2d'; g.fillStyle = '#ff2d2d';
      strokes.forEach((st) => { g.lineWidth = st.w; g.beginPath(); g.moveTo(st.p[0][0], st.p[0][1]); st.p.forEach(([x, y]) => g.lineTo(x, y)); if (st.p.length === 1) g.arc(st.p[0][0], st.p[0][1], st.w / 2, 0, 7); st.p.length === 1 ? g.fill() : g.stroke(); });
    };
    if (img.complete && img.naturalWidth) fit(); else img.onload = fit;
    const pos = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
    cv.onpointerdown = (e) => { e.preventDefault(); cv.setPointerCapture(e.pointerId); cur = { w: +br.value * cv.width / 600, p: [pos(e)] }; strokes.push(cur); redraw(); };
    cv.onpointermove = (e) => { if (!cur) return; cur.p.push(pos(e)); redraw(); };
    ['pointerup', 'pointercancel'].forEach((ev) => (cv['on' + ev] = () => { cur = null; }));
    $('#pundo', root).onclick = () => { strokes.pop(); redraw(); };
    $('#pclr', root).onclick = () => { strokes.length = 0; redraw(); };
    $('#pgo', root).onclick = async (e) => {
      const b = e.currentTarget;
      if (!strokes.length) { $('#perr', root).innerHTML = '<div class="note bad">Сначала закрасьте то, что нужно убрать</div>'; return; }
      b.disabled = true; b.innerHTML = '<span class="spin"></span>Убираем…'; $('#perr', root).innerHTML = '';
      try {
        await api(`/projects/${project.id}/photos/${ph.id}/erase`, { method: 'POST', body: { mask: cv.toDataURL('image/png') } });
        closeSheet(true); toast('Готово. Оригинал сохранён, можно вернуть'); await done();
      } catch (x) { b.disabled = false; b.textContent = 'Убрать закрашенное'; $('#perr', root).innerHTML = `<div class="note bad">${esc(x.message)}</div>`; }
    };
  }, () => URL.revokeObjectURL(url));
}

/* ---------- AI-студия ---------- */
const STYLES = [['none', 'Без стиля', 'image'], ['cinema', 'Кино', 'film'], ['illustration', 'Иллюстрация', 'image'], ['retro', 'Ретро', 'camera']];
const GSTYLES = [['photo', 'Фото', 'image'], ['cinema', 'Кино', 'film'], ['illustration', 'Иллюстрация', 'image'], ['retro', 'Ретро', 'camera']];
const ARROW = '<svg class="arr" viewBox="0 0 44 26" aria-hidden="true"><path d="M3 16c10 7 22 7 32-3" fill="none" stroke="#ffda24" stroke-width="6" stroke-linecap="round"/><path d="m28 6 10 5-6 10" fill="none" stroke="#ffda24" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
async function stepAI(project) {
  const mode = state.q.get('mode') || (project.photos.length ? 'enhance' : 'create');
  const idx = Math.max(0, Math.min(Math.max(project.photos.length - 1, 0), +(state.q.get('i') || 0)));
  let style = 'none', gstyle = 'photo', gen = null, busy = false, afterUrl = null, recent = [];
  shell(`${head('AI-студия', { back: `/project/${project.id}/photos` })}<div id="ai"></div>`, { noNav: true, hasBack: true });
  const box = $('#ai');
  const seg = `<div class="seg" style="margin-bottom:6px"><a class="${mode === 'enhance' ? 'on' : ''}" href="#/project/${project.id}/ai?mode=enhance&i=${idx}">${ic('spark', 20)}Улучшить</a><a class="${mode === 'create' ? 'on' : ''}" href="#/project/${project.id}/ai?mode=create&i=${idx}">${ic('spark', 20)}Создать</a></div>`;
  const styleChips = (list, cur, attr) => `<div class="seg" style="margin:10px 0">${list.map(([v, l, i]) => `<button class="${cur === v ? 'on' : ''}" data-${attr}="${v}" style="${cur === v ? 'background:var(--y-soft)' : ''}">${ic(i, 20)}${l}</button>`).join('')}</div>`;

  async function drawEnhance() {
    if (!project.photos.length) { box.innerHTML = `${seg}${tip('robot', 'Сначала добавьте фото, и я помогу их улучшить.', 'ai0')}<a class="btn" href="#/project/${project.id}/photos">Добавить фотографии</a>`; return; }
    const ph = project.photos[idx];
    box.innerHTML = `<div class="ba"><figure><img src="${ph.orig}" alt="Фото до улучшения"><figcaption>До</figcaption></figure><figure><img id="after" alt="Фото после улучшения"><figcaption>После</figcaption></figure>${ARROW}</div>
    ${seg}
    <div class="strip">${project.photos.map((p, i) => `<button class="${i === idx ? 'on' : ''}" data-i="${i}" aria-label="Фото ${i + 1}"><img src="${p.thumb}" alt=""></button>`).join('')}</div>
    <label class="lb">Стиль</label>${styleChips(STYLES, style, 'st')}
    ${tip('robot', 'Подберу свет и контраст, сверху добавлю стиль. Это обычная обработка: оригинал сохранится.', 'ai1')}
    <button class="btn" data-act="enh">Применить к фото</button>
    ${state.caps.upscale ? `<button class="btn line" data-act="upq">${ic('spark')}Улучшить качество (AI) · ${ph.width}×${ph.height}</button><p class="why">Нейросеть увеличит и сделает фото чётче. Подойдёт для маленьких и смазанных снимков.</p>` : ''}`;
    $$('[data-i]', box).forEach((b) => (b.onclick = () => go(`/project/${project.id}/ai?mode=enhance&i=${b.dataset.i}`)));
    $$('[data-st]', box).forEach((b) => (b.onclick = () => { style = b.dataset.st; drawEnhance(); }));
    try {
      const url = await apiBlob(`/projects/${project.id}/photos/${ph.id}/preview`, { edits: { auto: true, ...(style !== 'none' ? { filter: style } : {}) }, size: 700 });
      if (afterUrl) URL.revokeObjectURL(afterUrl); afterUrl = url; const im = $('#after'); if (im) im.src = url;
    } catch (x) { toast(x.message); }
  }
  function drawCreate() {
    const on = state.caps.ai_generate;
    const local = state.caps.ai_local;
    box.innerHTML = `${gen ? `<div class="ba one"><figure><img src="${gen.url}" alt="Созданный кадр"><figcaption>Результат</figcaption></figure></div>${gen.prompt ? `<p class="muted" style="margin:6px 0 0">«${esc(gen.prompt)}»</p>` : ''}` : ''}
    ${recent.length > 1 ? `<label class="lb">Недавние кадры</label><div class="strip">${recent.map((r, i) => `<button class="${gen && r.tmp_id === gen.tmp_id ? 'on' : ''}" data-rg="${i}" aria-label="Кадр ${i + 1}"><img src="${r.url}" alt=""></button>`).join('')}</div>` : ''}
    ${recent.length ? '<p class="why" style="margin-top:4px">Созданные кадры сохраняются здесь, даже если закрыть приложение. Добавьте лучший в альбом.</p>' : ''}
    ${seg}
    <label class="lb" for="pr">Опиши свою идею</label>
    <p class="why" style="margin:-4px 0 8px">Лучше коротко: кто, где и пара деталей, например «кот спит на подоконнике, зима за окном». Реальных людей по имени нейросеть не рисует: опишите внешность.</p>
    <div class="field"><textarea id="pr" rows="2" maxlength="160" placeholder="Девушка с мороженым на берегу моря" style="padding-right:48px">${esc(state.lastPrompt || '')}</textarea><button class="clr" data-act="clrprompt" aria-label="Очистить">${ic('close', 20)}</button></div>
    ${styleChips(GSTYLES, gstyle, 'gs')}
    ${tip('think', 'Создай уникальный кадр в своём стиле!', 'ai2')}
    <div id="gerr"></div>
    <button class="btn" data-act="make" ${on && !busy ? '' : 'disabled'}>${busy ? '<span class="spin"></span>Создаём…' : gen ? 'Создать другой вариант' : 'Создать'}</button>
    <button class="btn line" data-act="accept" ${gen && !busy ? '' : 'disabled'}>Добавить в альбом</button>
    <p class="why">${!on ? 'Генерация отключена в настройках сервера.' : local ? 'Картинку рисует нейросеть на сервере picPac: описание и фото никуда не передаются. Размер 768 px: подойдёт для небольшого формата.' : 'Картинку рисует внешний бесплатный сервис Pollinations. Ему уходит только текст описания, ваши фото не передаются. Размер 1024 px: подойдёт для небольшого формата.'}</p>`;
    $$('[data-gs]', box).forEach((b) => (b.onclick = () => { state.lastPrompt = $('#pr').value; gstyle = b.dataset.gs; drawCreate(); }));
    $$('[data-rg]', box).forEach((b) => (b.onclick = () => { state.lastPrompt = $('#pr').value; gen = recent[+b.dataset.rg]; drawCreate(); }));
  }
  if (mode === 'create') {  // кадры, созданные раньше, хранятся на сервере: показываем последний, чтобы ничего не терялось
    try { recent = await api(`/projects/${project.id}/ai/recent`); } catch { recent = []; }
    if (!gen && recent.length) gen = recent[0];
  }
  (mode === 'create' ? drawCreate : drawEnhance)();
  state.ctx = {
    clrprompt: () => { state.lastPrompt = ''; $('#pr').value = ''; $('#pr').focus(); },
    enh: async (el) => {
      el.disabled = true; el.innerHTML = '<span class="spin"></span>Сохраняем…';
      try {
        const ph = project.photos[idx]; const e = { ...(ph.edits || {}), auto: true };
        if (style !== 'none') e.filter = style; else delete e.filter;
        await api(`/projects/${project.id}/photos/${ph.id}/edit`, { method: 'PUT', body: { edits: e } });
        toast('Применено. Можно доработать в редакторе'); go(`/project/${project.id}/edit?i=${idx}`);
      } catch (x) { toast(x.message); el.disabled = false; el.textContent = 'Применить к фото'; }
    },
    upq: async (el) => { const ph = project.photos[idx]; if (await doUpscale(project, ph.id, Math.max(ph.width, ph.height) < 1400 ? 4 : 2, el)) go(`/project/${project.id}/edit?i=${idx}&tab=ai`); },
    make: async () => {
      const prompt = $('#pr').value.trim(); state.lastPrompt = prompt;
      if (prompt.length < 3) { $('#gerr').innerHTML = '<div class="note bad">Опишите идею хотя бы в нескольких словах</div>'; return; }
      busy = true; drawCreate(); $('#gerr').innerHTML = `<p class="muted">${state.caps.ai_local ? 'Обычно 5–20 секунд. Если кадры создают сразу несколько человек, ваш встанет в очередь.' : 'Обычно это занимает до минуты.'}</p>`;
      try {
        gen = await api(`/projects/${project.id}/ai/generate`, { method: 'POST', body: { prompt, style: gstyle } });
        recent = [gen, ...recent.filter((r) => r.tmp_id !== gen.tmp_id)];
        busy = false; drawCreate();
      }
      catch (x) { busy = false; drawCreate(); $('#gerr').innerHTML = `<div class="note bad"><b>Не получилось создать кадр.</b> ${esc(x.message)}</div>`; }
    },
    accept: async (el) => {
      el.disabled = true;
      try { await api(`/projects/${project.id}/ai/accept`, { method: 'POST', body: { tmp_id: gen.tmp_id, title: 'AI: ' + (gen.prompt || state.lastPrompt || '').slice(0, 40) } }); state.lastPrompt = ''; toast('Кадр добавлен в альбом'); go(`/project/${project.id}/photos`); }
      catch (x) { toast(x.message); el.disabled = false; }
    },
  };
}

/* ---------- готовый альбом ---------- */
const PAGE_THEMES = [['classic', 'Классика', 'Чисто и просто'], ['kids', 'Детский', 'Звёздочки, облака, рамки'], ['travel', 'Путешествие', 'Фото-карточки на крафте'],
  ['party', 'Праздник', 'Яркий коллаж с конфетти'], ['soft', 'Нежный', 'Мягкие цвета, круглые фото']];
let curTheme = 'classic';
function pageHtml(pg, byId, k = 'url') {
  const th = `th-${curTheme}`;
  if (!pg) return `<div class="pg t-blank ${th}"></div>`;
  if (pg.cover) return `<div class="pg t-cover ${th}"><b>${esc(pg.title)}</b>${pg.photo ? `<img src="${pg.photo[k]}" alt="">` : ''}</div>`;
  const imgs = pg.photo_ids.filter((i) => byId[i]).map((i) => `<img src="${byId[i][k]}" alt="">`).join('');
  return `<div class="pg t-${pg.template} v${pg.variant || 0} ${th}">${pg.template === 'blank' ? 'пусто' : imgs}${pg.caption ? `<span class="cap">${esc(fmtDate(pg.caption))}</span>` : ''}</div>`;
}
async function stepLayout(project) {
  const incl = project.photos.filter((p) => !p.excluded);
  if (!incl.length) return go(`/project/${project.id}/photos`);
  let fresh = false;
  if (!project.layout) {
    shell(`${head(project.title, { back: `/project/${project.id}/edit` })}<div class="foot" style="margin-top:80px"><span class="spin"></span>Создаём макет…</div>`, { noNav: true, hasBack: true });
    /* Число страниц подбираем под число фото, чтобы в книге не было пустых страниц. Поменять можно в «Стиль». */
    try { const sg = await api(`/projects/${project.id}/suggest-pages`); if (sg.pages && sg.pages !== project.settings.pages) await api('/projects/' + project.id, { method: 'PATCH', body: { settings: { pages: sg.pages } } }); } catch { /* оставим число страниц как было */ }
    await api(`/projects/${project.id}/automaket?sort=date`, { method: 'POST' });
    project = await api('/projects/' + project.id); fresh = true;
  }
  const chk = await api(`/projects/${project.id}/check`);
  const byId = Object.fromEntries(project.photos.map((p) => [p.id, p]));
  const lay = project.layout, pages = lay ? lay.pages : [];
  curTheme = (lay && lay.theme) || 'classic';
  /* Первый «разворот» — обложка; дальше страницы парами, как в раскрытой книге. Всё строится из реального макета. */
  const spreads = [[null, { cover: true, title: project.title, photo: incl[0] }]];
  for (let i = 0; i < pages.length; i += 2) spreads.push([pages[i], pages[i + 1] || null]);
  let cur = Math.max(0, Math.min(spreads.length - 1, +(state.q.get('s') || 0)));
  const nw = chk.warnings.length;
  const okAll = chk.ok && !nw;
  const blank = pages.filter((pg) => !pg.photo_ids.length).length;
  shell(`${head(project.title, { back: `/project/${project.id}/edit`, right: `<button class="ib r" data-act="cover" aria-label="Переименовать и выбрать обложку">${ic('pencil')}</button>` })}
  ${fresh ? tip('robot', `Разложил ${incl.length} фото на ${project.settings.pages} стр. и сгруппировал по дате съёмки. Листайте развороты.`, 'lay1') : ''}
  ${lay && lay.unplaced ? `<div class="note bad"><b>Не помещаются ${lay.unplaced} фото.</b> Увеличьте число страниц.<button class="btn sm line" data-act="style">Изменить число страниц</button></div>` : ''}
  ${blank ? `<div class="note y"><b>${blank} ${plural(blank, 'страница пустая', 'страницы пустые', 'страниц пустые')}.</b> В этой книге минимум ${project.product.pages.min} стр. Добавьте ещё фото, чтобы заполнить их.<a class="btn sm line" href="#/project/${project.id}/photos">Добавить фото</a></div>` : ''}
  <div class="bookview"><div class="spread" id="spread"></div><button class="fab l" data-act="prev" aria-label="Предыдущий разворот">${ic('back')}</button><button class="fab rr" data-act="nextsp" aria-label="Следующий разворот">${ic('next')}</button></div>
  <div class="dots" id="dots"></div>
  <div class="strip sp" id="spstrip">${spreads.map((s, i) => `<button data-s="${i}" aria-label="${i ? `Разворот ${i}` : 'Обложка'}"><span class="mini-sp">${pageHtml(s[0], byId, 'thumb')}${pageHtml(s[1], byId, 'thumb')}</span></button>`).join('')}</div>
  <div class="two" style="margin-top:8px"><button class="rowbtn" data-act="cover">${ic('book')}<span class="g">Обложка</span>${ic('next', 20)}</button><button class="rowbtn" data-act="style">${ic('layers')}<span class="g">Стиль</span>${ic('next', 20)}</button></div>
  <div class="statusrow">${okAll ? `<span><i class="dot">${ic('check', 16, 3)}</i>Макет проверен</span>` : `<a href="#/project/${project.id}/check"><i class="dot w">${ic('warn', 15, 2.4)}</i>${chk.ok ? `${nw} ${plural(nw, 'замечание', 'замечания', 'замечаний')} ›` : 'Нужно исправить ›'}</a>`}<span class="sv">Все изменения<br>сохранены ✓</span></div>
  <button class="btn" data-act="tocart" ${lay && !lay.unplaced ? '' : 'disabled'}>${okAll ? 'В корзину' : 'Проверить книгу'}</button>`, { noNav: true, hasBack: true });
  const draw = () => {
    const s = spreads[cur];
    $('#spread').innerHTML = pageHtml(s[0], byId) + pageHtml(s[1], byId);
    $('#dots').innerHTML = spreads.length <= 9 ? spreads.map((_, i) => `<i class="${i === cur ? 'on' : ''}"></i>`).join('') : '';
    $('#dots').insertAdjacentHTML('beforeend', `<span style="margin-left:8px">${cur ? `Разворот ${cur} из ${spreads.length - 1}` : 'Обложка'}</span>`);
    $$('#spstrip button').forEach((b, i) => b.classList.toggle('on', i === cur));
    $('[data-act=prev]').disabled = cur === 0; $('[data-act=nextsp]').disabled = cur === spreads.length - 1;
    const on = $('#spstrip .on'); if (on && on.scrollIntoView) on.scrollIntoView({ inline: 'center', block: 'nearest' });
  };
  draw();
  $$('#spstrip button').forEach((b) => (b.onclick = () => { cur = +b.dataset.s; draw(); }));
  let x0 = null; const sp = $('#spread');
  sp.addEventListener('touchstart', (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  sp.addEventListener('touchend', (e) => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 40) { cur = Math.max(0, Math.min(spreads.length - 1, cur + (dx < 0 ? 1 : -1))); draw(); } });
  const again = () => go(`/project/${project.id}/layout?s=${cur}`);
  state.ctx = {
    prev: () => { if (cur > 0) { cur--; draw(); } },
    nextsp: () => { if (cur < spreads.length - 1) { cur++; draw(); } },
    tocart: () => { track('check_passed'); go(`/project/${project.id}/${okAll || (chk.ok && acked(project)) ? 'cart' : 'check'}`); },
    cover: () => {
      const o = project.product.options; let cov = project.settings.cover;
      sheet(`<h2>Обложка</h2><label class="lb" for="ttl">Название на обложке</label><input id="ttl" type="text" maxlength="80" value="${esc(project.title)}">
      <label class="lb">Тип обложки</label><div class="opts" id="covs">${o.covers.map((c) => `<button class="opt ${c.id === cov ? 'on' : ''}" data-c="${c.id}">${esc(c.label)}</button>`).join('')}</div>
      <p class="muted">На обложке печатается первое фото проекта. Поменять его можно, изменив порядок на экране фотографий.</p><div id="cerr"></div><button class="btn" data-act="savecover">Сохранить</button>`, (root) => {
        $$('[data-c]', root).forEach((b) => (b.onclick = () => { cov = b.dataset.c; $$('[data-c]', root).forEach((x) => x.classList.toggle('on', x === b)); }));
        state.ctx.savecover = async (el) => {
          el.disabled = true; el.innerHTML = '<span class="spin"></span>Сохраняем…';
          try { await api('/projects/' + project.id, { method: 'PATCH', body: { title: $('#ttl').value, settings: { cover: cov } } }); closeSheet(true); again(); }
          catch (x) { el.disabled = false; el.textContent = 'Повторить'; $('#cerr').innerHTML = `<div class="note bad">Не удалось сохранить: ${esc(x.message)}</div>`; }
        };
      });
    },
    style: () => {
      sheet(`<h2>Стиль альбома</h2><label class="lb">Оформление страниц</label>
      <div class="themes">${PAGE_THEMES.map(([v, n, d]) => `<button class="th-pick ${curTheme === v ? 'on' : ''}" data-theme="${v}"><span class="pg t-grid v1 th-${v}" aria-hidden="true"><i></i><i></i><i></i><i></i></span><b>${n}</b><small>${d}</small></button>`).join('')}</div>
      <label class="lb">Порядок фотографий</label>
      <div class="stack"><button class="rowbtn" data-sort="date">${ic('cal')}<span class="g">По дате съёмки<small>Группируем события по дням</small></span></button><button class="rowbtn" data-sort="manual">${ic('layers')}<span class="g">Мой порядок<small>Как на экране фотографий</small></span></button>
      <button class="rowbtn" data-act="closesheet" id="topar">${ic('book')}<span class="g">Формат и число страниц<small>${esc(paramsLabel(project))}</small></span>${ic('next', 20)}</button></div><div id="serr"></div>`, (root) => {
        $$('[data-theme]', root).forEach((b) => (b.onclick = async () => {
          if (b.dataset.theme === curTheme) return closeSheet();
          $$('[data-theme]', root).forEach((x) => (x.disabled = true));
          try { await api(`/projects/${project.id}/automaket?sort=${(project.layout && project.layout.sort) || 'date'}&theme=${b.dataset.theme}`, { method: 'POST' }); closeSheet(true); toast('Оформление применено'); again(); }
          catch (x) { $$('[data-theme]', root).forEach((y) => (y.disabled = false)); $('#serr').innerHTML = `<div class="note bad">${esc(x.message)}</div>`; }
        }));
        $$('[data-sort]', root).forEach((b) => (b.onclick = async () => {
          b.disabled = true;
          try { await api(`/projects/${project.id}/automaket?sort=${b.dataset.sort}`, { method: 'POST' }); closeSheet(true); toast('Макет пересобран'); again(); }
          catch (x) { b.disabled = false; $('#serr').innerHTML = `<div class="note bad">${esc(x.message)}</div>`; }
        }));
        $('#topar', root).addEventListener('click', () => setTimeout(() => paramsSheet(project, again), 50));
      });
    },
  };
}

/* ---------- проверка перед печатью ---------- */
const FIXES = { replace: 'Заменить фото', enhance: 'Улучшить', remove: 'Убрать', upscale: 'Улучшить качество (AI)' };
const EXPLAIN = {
  low_resolution: 'Это фото может получиться нечётким при печати: в нём мало пикселей для выбранного формата.',
  blurry: 'Кадр смазан. «Улучшить» добавит резкости, но полностью не исправит.',
  too_dark: 'Фото слишком тёмное, детали могут пропасть. «Улучшить» поднимет яркость.',
  overexposed: 'Светлые места «выгорели»: на печати там не будет деталей. «Улучшить» слегка притемнит кадр, но потерянное не вернуть.',
  low_contrast: 'Кадр выглядит блёклым. «Улучшить» добавит контраста.',
  noisy: 'Заметно цифровое зерно, обычно от съёмки в темноте. На бумаге оно проявится сильнее.',
  extreme_aspect: 'Очень вытянутый кадр. На странице будут большие поля или он сильно обрежется.',
  enhanced: 'Фото улучшено нейросетью: резкость на печати выше, чем показывает проверка по уменьшенному кадру.',
  compressed: 'Файл сильно сжат, например, из мессенджера. Если есть оригинал, загрузите его: качество будет лучше.',
  transparent: 'В файле есть прозрачность. При печати она станет белым фоном.',
  format_converted: 'Формат пересохранён в JPEG. Цвета могут чуть отличаться, на печати это обычно незаметно.',
};
async function stepCheck(project) {
  const chk = await takeCheck(project);
  const byId = Object.fromEntries(project.photos.map((p, i) => [p.id, { ...p, i }]));
  const pageOf = {};
  if (project.layout) project.layout.pages.forEach((pg) => pg.photo_ids.forEach((i) => { pageOf[i] = pg.page; }));
  const ack = acked(project);
  const nw = chk.warnings.length;
  const back = `/project/${project.id}/${isBook(project) ? 'layout' : 'edit'}`;
  shell(`${head('Проверим перед печатью', { back })}
  ${chk.ok && !nw ? `<div class="note ok"><b>Всё в порядке.</b> Разрешения хватает, фото чёткие и не пересвечены, похожих кадров нет${isBook(project) ? ', все страницы заполнены' : ''}.</div>` : tip('robot', nw ? `Нашёл ${nw} ${plural(nw, 'место', 'места', 'мест')}, где печать может разочаровать. Покажу каждое.` : 'Сначала исправьте отмеченное ниже.', 'chk')}
  ${chk.blocking.map((b) => `<div class="note bad"><b>Нужно исправить:</b> ${esc(b.message)}</div>`).join('')}
  ${cleanCard(project, chk)}
  ${chk.warnings.map((w) => `<div class="wi">${w.photo_id && byId[w.photo_id] ? `<img src="${byId[w.photo_id].thumb}" alt="Фото ${byId[w.photo_id].i + 1}">` : ''}<div class="g"><b>${w.photo_id && byId[w.photo_id] ? `Фото ${byId[w.photo_id].i + 1}${pageOf[w.photo_id] ? ` · стр. ${pageOf[w.photo_id]}` : ''}` : 'Макет'}</b><span class="muted">${esc(EXPLAIN[w.code] || w.message)}</span>${!w.photo_id && w.code === 'blank_pages' ? `<div class="acts"><a class="btn sm line" href="#/project/${project.id}/photos">Добавить фото</a></div>` : ''}${w.photo_id ? `<div class="acts">${(state.caps.upscale && ['low_resolution', 'blurry'].includes(w.code) ? [...w.fix, 'upscale'] : w.fix).map((f) => `<button class="btn sm ${f === 'remove' ? 'grey' : f === 'upscale' ? '' : 'line'}" data-act="fix" data-fix="${f}" data-id="${w.photo_id}">${FIXES[f]}</button>`).join('')}<a class="btn sm grey" href="#/project/${project.id}/edit?i=${byId[w.photo_id].i}&tab=crop">Открыть</a></div>` : ''}</div></div>`).join('')}
  ${chk.tips && chk.tips.length ? `<h3 style="margin:18px 0 8px">Советы</h3><p class="muted" style="margin:0 0 8px">Заказ они не задерживают.</p>${chk.tips.map((w) => `<div class="wi">${byId[w.photo_id] ? `<img src="${byId[w.photo_id].thumb}" alt="Фото ${byId[w.photo_id].i + 1}">` : ''}<div class="g"><b>Фото ${byId[w.photo_id] ? byId[w.photo_id].i + 1 : ''}</b><span class="muted">${esc(EXPLAIN[w.code] || w.message)}</span>${w.fix.length ? `<div class="acts">${w.fix.map((f) => `<button class="btn sm line" data-act="fix" data-fix="${f}" data-id="${w.photo_id}">${FIXES[f]}</button>`).join('')}</div>` : ''}</div></div>`).join('')}` : ''}
  <input id="repl" class="hide" type="file" accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif,image/webp,image/tiff,image/bmp">
  ${nw && chk.ok ? `<label class="ack"><input type="checkbox" id="ack" ${ack ? 'checked' : ''}><span>Оставить как есть: я понимаю, что эти фото могут напечататься хуже</span></label>` : ''}
  <p class="muted">Цвет на экране и на бумаге может немного отличаться.</p>
  <button class="btn" data-act="next" ${chk.ok && (!nw || ack) ? '' : 'disabled'}>Перейти к заказу</button>
  <p class="why" id="whyno">${!chk.ok ? 'Сначала исправьте отмеченное красным' : nw && !ack ? 'Исправьте фото или отметьте «Оставить как есть»' : ''}</p>`, { noNav: true, hasBack: true });
  const ackEl = $('#ack'); if (ackEl) ackEl.onchange = () => { store.set('pp_ack_' + project.id, ackEl.checked ? '1' : '0'); $('[data-act=next]').disabled = !ackEl.checked; $('#whyno').textContent = ackEl.checked ? '' : 'Исправьте фото или отметьте «Оставить как есть»'; };
  let target = null;
  $('#repl').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const fd = new FormData(); fd.append('files', f, f.name);
      const r = await api(`/projects/${project.id}/photos`, { method: 'POST', form: fd });
      if (r.rejected.length) return toast(r.rejected[0].reason);
      await api(`/projects/${project.id}/photos/${target}`, { method: 'DELETE' });
      toast('Фото заменено'); await viewProject(project.id, 'check');
    } catch (x) { toast(x.message); }
  };
  state.ctx = {
    clean: (el) => doCleanup(project, el.dataset.kind, () => viewProject(project.id, 'check')),
    next: () => { track('check_passed'); go(`/project/${project.id}/cart`); },
    fix: async (el) => {
      const id = el.dataset.id, f = el.dataset.fix;
      try {
        if (f === 'replace') { target = id; $('#repl').click(); return; }
        if (f === 'upscale') { const ph = project.photos.find((x) => x.id === id); const r = await doUpscale(project, id, Math.max(ph.width, ph.height) < 1400 ? 4 : 2, el); if (r) await viewProject(project.id, 'check'); return; }
        el.disabled = true;
        if (f === 'remove') { await api(`/projects/${project.id}/photos/${id}`, { method: 'DELETE' }); toast('Фото убрано из проекта'); }
        if (f === 'enhance') { await api(`/projects/${project.id}/photos/${id}/enhance`, { method: 'POST' }); toast('Фото улучшено. Оригинал сохранён'); }
        await viewProject(project.id, 'check');
      } catch (x) { el.disabled = false; toast(x.message); }
    },
  };
}

/* ---------- корзина и доставка ---------- */
const DELIVERY = { pvz: ['Пункт выдачи', 'pin'], courier: ['Курьер', 'truck'], belpost: ['Почта', 'box'] };
const minutesNowMinsk = () => { const p = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Minsk', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()).split(':'); return +p[0] * 60 + +p[1]; };
const isOpenNow = (hours) => { const m = /(\d\d):(\d\d)\D+(\d\d):(\d\d)/.exec(hours || ''); if (!m) return true; const n = minutesNowMinsk(); return n >= +m[1] * 60 + +m[2] && n < +m[3] * 60 + +m[4]; };
const pinIcon = (on) => `<svg width="${on ? 38 : 30}" height="${on ? 48 : 38}" viewBox="0 0 30 38"><path d="M15 37S2 24.500 2 14a13 13 0 0 1 26 0c0 10.500-13 23-13 23z" fill="#ffda24" stroke="#171717" stroke-width="2.200"/><circle cx="15" cy="14" r="${on ? 6 : 4.500}" fill="${on ? '#171717' : '#fff'}"${on ? '' : ' stroke="#171717" stroke-width="1.500"'}/></svg>`;

async function stepCart(project) {
  const chk = await takeCheck(project);
  if (!chk.ok || (chk.warnings.length && !acked(project))) return go(`/project/${project.id}/check`);
  const pts = await api('/pickup-points');
  let saved = {}; try { saved = JSON.parse(store.get('pp_dlv_' + project.id) || '{}'); } catch { /* ignore */ }
  const d = { method: 'pvz', pvz_id: null, recipient_name: state.user.guest ? '' : state.user.name, phone: '', address: '', ...saved };
  const persist = () => store.set('pp_dlv_' + project.id, JSON.stringify(d));
  let promo = store.get('pp_promo_' + project.id) || '';
  let list = pts, onlyOpen = false, origin = null, map = null, markers = {}, me = null, priceNow = null;
  shell(`${head('Корзина · 1', { back: `/project/${project.id}/${isBook(project) ? 'layout' : 'check'}` })}
  <button class="card row" style="width:100%;text-align:left" data-act="params">${book((project.photos.find((p) => !p.excluded) || {}).thumb, project.title, true)}<span class="g"><h3>${esc(project.title)}</h3><span class="muted">${esc(paramsLabel(project))}</span><br><b id="itemprice" style="font-size:18px">…</b></span>${ic('next')}</button>
  <div class="seg" id="methods" style="margin-top:12px">${Object.entries(DELIVERY).map(([k, [t, i]]) => `<button data-m="${k}">${ic(i, 20)}${t}</button>`).join('')}</div>
  <div id="pvzbox">
    <div id="map" aria-label="Карта пунктов выдачи"></div>
    <div class="stack">
      <button class="rowbtn" data-act="pickpvz">${ic('pin')}<span class="g" id="pvzlabel">Выбрать ПВЗ</span>${ic('next', 20)}</button>
      <form id="qf" class="field">${ic('search', 22)}<input id="q" type="text" placeholder="Город или адрес" aria-label="Город или адрес для поиска ближайших пунктов" enterkeyhint="search"><button type="button" class="clr" data-act="geo" aria-label="Найти рядом со мной">${ic('locate', 22)}</button></form>
    </div>
    <div id="qmsg"></div>
  </div>
  <div id="addrbox" class="hide"><label class="lb" for="addr">Адрес доставки</label><input id="addr" type="text" autocomplete="street-address" placeholder="Город, улица, дом, квартира"></div>
  <div class="two" style="margin-top:12px"><div><label class="lb" for="rn" style="margin-top:0">Получатель</label><input id="rn" type="text" autocomplete="name" placeholder="Имя и фамилия"></div><div><label class="lb" for="ph" style="margin-top:0">Телефон</label><input id="ph" type="tel" autocomplete="tel" placeholder="+375 29 …"></div></div>
  <div class="stack" style="margin-top:12px"><button class="rowbtn" data-act="promo">${ic('ticket')}<span class="g" id="promolabel">Промокод</span>${ic('next', 20)}</button></div>
  <details style="margin-top:12px"><summary class="muted" style="cursor:pointer">Из чего складывается цена</summary><div id="sum"></div></details>
  <div class="total"><span>Итого</span><span id="total">…</span></div>
  <div id="oerr"></div>
  <button class="btn" data-act="order" id="obtn">Создать тестовый заказ</button>
  <div class="foot" style="margin-bottom:0">${ic('info', 18)}Демонстрационные данные, деньги не списываются</div>`, { noNav: true, hasBack: true });

  const shown = () => list.filter((p) => !onlyOpen || isOpenNow(p.hours));
  const pvzById = (id) => pts.find((p) => p.id === id);
  const sync = () => {
    $$('[data-m]').forEach((b) => b.classList.toggle('on', b.dataset.m === d.method));
    $('#pvzbox').classList.toggle('hide', d.method !== 'pvz'); $('#addrbox').classList.toggle('hide', d.method === 'pvz');
    const p = d.pvz_id && pvzById(d.pvz_id);
    $('#pvzlabel').innerHTML = p ? `Ваш пункт выдачи<small>${esc(p.name)}, ${esc(p.address)}</small>` : 'Выбрать ПВЗ';
    $('#promolabel').innerHTML = promo ? `Промокод<small>${esc(promo)}</small>` : 'Промокод';
    if (map) Object.entries(markers).forEach(([id, m]) => { m.setIcon(L.divIcon({ className: '', html: pinIcon(id === d.pvz_id), iconSize: id === d.pvz_id ? [38, 48] : [30, 38], iconAnchor: id === d.pvz_id ? [19, 46] : [15, 36] })); m.setZIndexOffset(id === d.pvz_id ? 500 : 0); });
  };
  const choose = (id, pan) => { d.pvz_id = id; persist(); sync(); const m = markers[id]; if (pan && m && map) map.setView(m.getLatLng(), Math.max(map.getZoom(), 13)); };
  if (window.L) {
    map = L.map('map', { scrollWheelZoom: false, zoomControl: false }).setView([53.9045, 27.5615], 11);
    map.attributionControl.setPrefix(false);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
    pts.forEach((p) => { markers[p.id] = L.marker([p.lat, p.lon], { title: p.name, keyboard: false, icon: L.divIcon({ className: '', html: pinIcon(false), iconSize: [30, 38], iconAnchor: [15, 36] }) }).addTo(map).on('click', () => choose(p.id)); });
  } else $('#map').innerHTML = '<p class="muted" style="padding:14px">Карта недоступна. Нажмите «Выбрать ПВЗ», чтобы открыть список.</p>';
  $('#addr').value = d.address || ''; $('#rn').value = d.recipient_name || ''; $('#ph').value = d.phone || '';
  [['#addr', 'address'], ['#rn', 'recipient_name'], ['#ph', 'phone']].forEach(([s, k]) => ($(s).oninput = (e) => { d[k] = e.target.value; persist(); }));
  const calc = async () => {
    try {
      const r = await api('/price', { method: 'POST', body: { product_id: project.product.id, settings: project.settings, project_id: project.id, promo: promo || null, delivery: d.method } });
      priceNow = r;
      $('#itemprice').textContent = byn(r.subtotal); $('#total').textContent = byn(r.total);
      $('#sum').innerHTML = `<table class="sum">${r.lines.map((l) => `<tr><td>${esc(l.title)}${l.qty > 1 ? ` × ${l.qty}` : ''}</td><td>${byn(l.sum)}</td></tr>`).join('')}${r.discount ? `<tr><td>Скидка по промокоду ${esc(r.promo)}</td><td>−${byn(r.discount)}</td></tr>` : ''}<tr><td>${esc(r.delivery_label)}</td><td>${r.delivery_cost ? byn(r.delivery_cost) : 'бесплатно'}</td></tr></table><p class="muted">Срок изготовления: ${r.production_days} дн.</p>`;
      return r;
    } catch (x) { $('#total').textContent = '—'; $('#sum').innerHTML = `<div class="note bad">${esc(x.message)}</div>`; return null; }
  };
  $$('[data-m]').forEach((b) => (b.onclick = () => { d.method = b.dataset.m; persist(); sync(); calc(); if (map) setTimeout(() => map.invalidateSize(), 50); }));
  const useOrigin = async (lat, lon, label) => {
    origin = { lat, lon }; list = await api(`/pickup-points?lat=${lat}&lon=${lon}`);
    if (map) { if (me) me.remove(); me = L.circleMarker([lat, lon], { radius: 8, color: '#fff', weight: 3, fillColor: '#22c3dd', fillOpacity: 1 }).addTo(map); map.setView([lat, lon], 12); }
    $('#qmsg').innerHTML = `<p class="muted">Ближайшие к: ${esc(label)}. Расстояние считаем по прямой.</p>`;
    pvzSheet();
  };
  const find = async () => {
    const q = $('#q').value.trim(); if (q.length < 3) return toast('Введите город или адрес');
    $('#qmsg').innerHTML = '<p class="muted"><span class="spin"></span>Ищем адрес…</p>';
    try {
      const r = await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=4&countrycodes=by&accept-language=ru&q=' + encodeURIComponent(q));
      const res = await r.json();
      const short = (x, n) => x.display_name.split(',').slice(0, n).join(',');
      if (!res.length) { $('#qmsg').innerHTML = '<div class="note y">Адрес не найден. Уточните запрос или выберите пункт на карте.</div>'; return; }
      if (res.length === 1) return useOrigin(+res[0].lat, +res[0].lon, short(res[0], 3));
      $('#qmsg').innerHTML = `<div class="note y"><b>Уточните адрес:</b><div class="stack" style="margin-top:8px">${res.map((x, i) => `<button class="rowbtn w" style="background:#fff" data-pick="${i}">${esc(short(x, 4))}</button>`).join('')}</div></div>`;
      $$('[data-pick]').forEach((b) => (b.onclick = () => { const x = res[+b.dataset.pick]; useOrigin(+x.lat, +x.lon, short(x, 3)); }));
    } catch { $('#qmsg').innerHTML = '<div class="note y">Поиск адреса сейчас недоступен. Откройте список пунктов: «Выбрать ПВЗ».</div>'; }
  };
  $('#qf').onsubmit = (e) => { e.preventDefault(); find(); };
  function pvzSheet() {
    const body = () => {
      const rows = shown(); const top = origin ? new Set(list.slice(0, 3).map((p) => p.id)) : new Set();
      return `<div class="seg" style="margin-bottom:6px"><button class="${onlyOpen ? '' : 'on'}" data-oo="0">Все</button><button class="${onlyOpen ? 'on' : ''}" data-oo="1">Открыто сейчас</button></div>
      ${rows.length ? rows.map((p) => `<label class="pvz ${d.pvz_id === p.id ? 'on' : ''} ${isOpenNow(p.hours) ? '' : 'off'}"><input type="radio" name="pvz" value="${p.id}" ${d.pvz_id === p.id ? 'checked' : ''}><span class="g"><b>${esc(p.name)}</b>${top.has(p.id) ? ' <span class="chip y">рядом</span>' : ''}<br><span class="muted">${esc(p.address)}${p.distance_km != null ? ` · ≈${p.distance_km} км` : ''}<br>${esc(p.hours)} · ${isOpenNow(p.hours) ? 'открыто' : 'сейчас закрыто'}</span></span></label>`).join('') : '<div class="note y">Сейчас все пункты закрыты. Переключите на «Все».</div>'}
      <p class="muted">Каталог пунктов тестовый: адреса условные.</p><button class="btn" data-act="closesheet">Готово</button>`;
    };
    sheet(`<h2>Пункт выдачи</h2>${state.hidden.has('cat1') ? '' : tip('cat', origin ? 'Показываю ближайшие сверху.' : 'Введите адрес или нажмите на прицел у поиска: покажу ближайшие.', 'cat1')}<div id="pvzl"></div>`, (root) => {
      const draw = () => {
        $('#pvzl', root).innerHTML = body();
        $$('input[name=pvz]', root).forEach((r) => (r.onchange = () => { choose(r.value, true); draw(); }));
        $$('[data-oo]', root).forEach((b) => (b.onclick = () => { onlyOpen = b.dataset.oo === '1'; draw(); }));
      };
      draw();
    });
  }
  sync(); await calc(); if (map) setTimeout(() => map.invalidateSize(), 120);
  const key = () => { let k = store.get('pp_idem_' + project.id); if (!k) { k = uid(); store.set('pp_idem_' + project.id, k); } return k; };
  const validate = () => {
    const e = [];
    if (d.method === 'pvz' && !d.pvz_id) e.push('Выберите пункт выдачи');
    if (d.method !== 'pvz' && !(d.address || '').trim()) e.push('Укажите адрес доставки');
    if ((d.recipient_name || '').trim().length < 2) e.push('Укажите имя получателя');
    if (!/^[0-9+()\- ]{7,20}$/.test(d.phone || '')) e.push('Телефон: от 7 знаков, только цифры, +, скобки и дефис');
    return e;
  };
  state.ctx = {
    params: () => paramsSheet(project, () => viewProject(project.id, 'cart')),
    pickpvz: pvzSheet,
    geo: () => {
      if (!navigator.geolocation) return toast('Геолокация недоступна на этом устройстве');
      navigator.geolocation.getCurrentPosition((pos) => useOrigin(pos.coords.latitude, pos.coords.longitude, 'вашему местоположению').catch((x) => toast(x.message)),
        () => { $('#qmsg').innerHTML = '<div class="note y">Нет доступа к геолокации. Введите адрес или выберите пункт из списка.</div>'; });
    },
    promo: () => {
      sheet(`<h2>Промокод</h2><input id="pc" type="text" placeholder="Например, TEST10" value="${esc(promo)}" autocapitalize="characters"><div id="perr"></div><p></p><button class="btn" data-act="applypromo">Применить</button>`, (root) => {
        state.ctx.applypromo = async () => {
          const old = promo; promo = $('#pc', root).value.trim();
          const r = await calc();
          if (promo && r && r.promo_error) { $('#perr', root).innerHTML = `<div class="note bad">${esc(r.promo_error)}</div>`; promo = old; await calc(); return; }
          store.set('pp_promo_' + project.id, promo); closeSheet(true); sync(); if (promo) toast('Промокод применён');
        };
      });
    },
    order: async (el) => {
      const e = validate();
      if (e.length) { $('#oerr').innerHTML = `<div class="note bad">${e.map(esc).join('<br>')}</div>`; return; }
      persist(); track('delivery_selected');
      el.disabled = true; el.innerHTML = '<span class="spin"></span>Оформляем…'; $('#oerr').innerHTML = '';
      const headers = { 'Idempotency-Key': key() };
      if (state.sim.loseResponse) { headers['X-Demo-Fail-After-Commit'] = '1'; state.sim.loseResponse = false; }
      try {
        const o = await api('/orders', { method: 'POST', headers, body: { project_id: project.id, delivery: d, promo: promo || null, accept_warnings: acked(project) } });
        ['pp_idem_', 'pp_dlv_', 'pp_ack_', 'pp_promo_'].forEach((p) => store.del(p + project.id));
        go('/order/' + o.id); toast(o.replayed ? `Тестовый заказ уже создан: ${o.number}. Второй не создаём` : 'Заказ принят!'); if (!o.replayed && window.ppConfetti) window.ppConfetti();
      } catch (x) {
        el.disabled = false; el.textContent = 'Повторить оформление';
        if (x instanceof NetErr || (x instanceof ApiErr && x.status >= 500)) $('#oerr').innerHTML = '<div class="note y"><b>Связь прервалась.</b> Проект и данные сохранены. Нажмите «Повторить оформление»: если заказ уже создан, покажем его, второй не появится.</div>';
        else $('#oerr').innerHTML = `<div class="note bad">${esc(x.message)}</div>`;
      }
    },
  };
}

/* ---------- заказы ---------- */
const orderCard = (o) => `<a class="card row" href="#/order/${o.id}">${book(o.cover, o.project_title)}<span class="g"><h3>${esc(o.project_title)}</h3><span class="muted">${esc(o.number)} · ${fmt(o.created_at)}</span><br><b>${byn(o.total)}</b></span><span class="chip ${chipClass(o.status)}">${esc(LABELS[o.status] || o.status_label)}</span></a>`;
async function viewOrders() {
  const done = state.q.get('f') === 'done';
  const all = await api('/orders');
  const orders = done ? all.filter((o) => o.status === 'completed') : all;
  shell(`${head(done ? 'Мои покупки' : 'Мои заказы', { back: '/profile' })}
  ${orders.length ? `<div class="stack">${orders.map(orderCard).join('')}</div>` : `${tip('cat', done ? 'Полученных заказов пока нет. Они появятся здесь после выдачи.' : 'Заказов пока нет. Соберите первую фотокнигу!', 'noord')}<a class="btn" href="#/new">Создать фотокнигу</a>`}`, { active: '/profile', hasBack: true });
}
function timeline(o) {
  const ev = Object.fromEntries(o.events.map((e) => [e.status, e]));
  const flow = o.status === 'rejected' ? ['accepted', 'moderation', 'rejected'] : FLOW;
  const curIdx = flow.indexOf(o.status);
  return `<ul class="tl">${flow.map((s, i) => {
    const done = i < curIdx || (s === 'completed' && s === o.status);
    return `<li class="${s === 'rejected' ? 'bad' : done ? 'done' : i === curIdx ? 'cur' : ''}"><i>${done ? ic('check', 15, 3.2) : s === 'rejected' ? '!' : ''}</i><span>${LABELS[s]}${ev[s] ? `<small>${fmt(ev[s].ts)}${ev[s].note ? ' · ' + esc(ev[s].note) : ''}</small>` : ''}</span></li>`;
  }).join('')}</ul>`;
}
async function viewOrder(id) {
  let o = await api('/orders/' + id);
  const draw = () => {
    const active = isActive(o);
    shell(`${head('Заказ ' + o.number, { back: '/orders' })}
    <div class="row">${book(o.cover, o.project_title, true)}<div class="card ${o.status === 'rejected' ? '' : 'y'} g row" style="min-height:84px${o.status === 'rejected' ? ';background:var(--bad-bg);border-color:transparent;color:var(--bad)' : ''}">${ic(o.status === 'completed' ? 'check' : o.status === 'rejected' ? 'warn' : o.status === 'ready' ? 'truck' : 'box', 34)}<b style="font-size:18px;line-height:1.2">${esc(LABELS[o.status] || o.status_label)}</b></div></div>
    ${V3 && o.status === 'accepted' ? tip('happy', 'Заказ принят! Ниже видно, на каком он этапе.', 'ord-' + o.id) : ''}
    ${timeline(o)}
    <div class="note y"><b>TEST-заказ.</b> Оплата не списывалась. Заказ не передан в реальное производство: это демонстрация сценария.</div>
    ${active && o.eta ? `<p class="muted" style="margin-top:-6px">Ожидаем готовность к ${fmtDate(o.eta)}</p>` : ''}
    ${o.tracking ? `<div class="card"><b>Трек-номер: ${esc(o.tracking.number)}</b><p class="muted" style="margin:6px 0 0">${esc(o.tracking.carrier)}. Номер демонстрационный, реальной отправки нет: по нему клиент отслеживал бы посылку у службы доставки.</p></div>` : ''}
    <div class="stack">
      <div class="rowbtn" style="min-height:64px">${ic(o.delivery.method === 'pvz' ? 'pin' : 'truck', 28)}<span class="g">${o.delivery.method === 'pvz' ? 'Ваш пункт выдачи' : 'Доставка'}<small style="white-space:normal">${o.delivery.method === 'pvz' ? esc((o.delivery.pvz_name || 'Пункт выдачи') + ', ' + (o.delivery.pvz_address || '')) : esc(o.delivery.address || '')}</small></span></div>
      <div class="row" style="padding:4px 2px">${ic('bell')}<span class="g" style="font-weight:700">Уведомим в Telegram<br><span class="muted" style="font-weight:500">${state.user.tg ? 'Пришлём сообщение, когда статус изменится.' : 'Откройте picPac из Telegram, чтобы получать сообщения о статусе. Пока статус обновляется на этом экране.'}</span></span><span class="sw ${state.user.tg ? 'on' : ''}" role="img" aria-label="${state.user.tg ? 'Включено' : 'Выключено'}"></span></div>
      <button class="btn line" data-act="support">${ic('chat')}Поддержка${o.tickets.length ? ` · ${o.tickets.length}` : ''}</button>
      <details><summary class="muted" style="cursor:pointer">Состав и стоимость: ${byn(o.total)}</summary><table class="sum">${o.price.lines.map((l) => `<tr><td>${esc(l.title)}${l.qty > 1 ? ` × ${l.qty}` : ''}</td><td>${byn(l.sum)}</td></tr>`).join('')}${o.price.discount ? `<tr><td>Скидка ${esc(o.promo || '')}</td><td>−${byn(o.price.discount)}</td></tr>` : ''}<tr><td>${esc(o.price.delivery_label)}</td><td>${o.price.delivery_cost ? byn(o.price.delivery_cost) : 'бесплатно'}</td></tr></table><p class="muted">Получатель: ${esc(o.delivery.recipient_name)}</p></details>
      ${!active ? '<button class="btn" data-act="reorder">Заказать ещё</button>' : ''}
    </div>
    <div class="foot">${ic('info', 18)}Демо-статусы: заказ двигает оператор</div>`, { active: '/profile', hasBack: true });
  };
  draw();
  const support = (prefix = '') => {
    sheet(`<h2>Поддержка по заказу</h2>${o.tickets.map((t) => `<div class="card" style="margin-bottom:8px"><span class="muted">${fmt(t.created_at)} · ${t.status === 'open' ? 'в работе' : 'закрыто'}</span><br>${esc(t.text)}</div>`).join('')}
    <label class="lb" for="tt">Ваш вопрос или отзыв</label><textarea id="tt" rows="3" maxlength="1000" placeholder="Например: хочу изменить пункт выдачи">${esc(prefix)}</textarea><div id="terr"></div><p></p><button class="btn" data-act="ticket">Отправить</button>`, (root) => {
      state.ctx.ticket = async (el) => {
        const t = $('#tt', root).value.trim(); if (t.length < 3) { $('#terr', root).innerHTML = '<div class="note bad">Опишите вопрос подробнее</div>'; return; }
        el.disabled = true;
        try { o = await api(`/orders/${id}/tickets`, { method: 'POST', body: { text: t } }); closeSheet(true); draw(); toast('Обращение отправлено'); }
        catch (x) { el.disabled = false; $('#terr', root).innerHTML = `<div class="note bad">${esc(x.message)}</div>`; }
      };
    });
  };
  state.ctx = { support: () => support(), reorder: (el) => reorder(o.project_id, el) };
  if (state.q.get('review')) support('Отзыв: ');
  state.poll = setInterval(async () => {
    if ($('.sheet-bg')) return;
    try { const n = await api('/orders/' + id); if (n.status !== o.status || n.tickets.length !== o.tickets.length) { o = n; draw(); } } catch { /* нет сети — попробуем позже */ }
  }, 4000);
}
async function reorder(projectId, el) {
  if (el) { el.disabled = true; el.innerHTML = '<span class="spin"></span>Создаём копию…'; }
  try {
    const p = await api(`/projects/${projectId}/clone`, { method: 'POST' });
    toast('Создали копию проекта: можно заказать ещё раз');
    go(`/project/${p.id}/${p.product.kind === 'book' ? 'layout' : 'photos'}`);
  } catch (x) { if (el) { el.disabled = false; el.textContent = 'Заказать ещё'; } toast(x instanceof ApiErr && x.status === 404 ? 'Исходный проект удалён, повторить нельзя' : x.message); }
}

/* ---------- профиль ---------- */
async function viewProfile() {
  const u = state.user, op = u.role === 'operator';
  const orders = op ? [] : await api('/orders');
  const last = orders.find((o) => o.status === 'completed') || orders[0];
  const check = (id, on, text) => `<label class="ack" style="margin:10px 0"><input type="checkbox" id="${id}" ${on ? 'checked' : ''}><span style="font-weight:500">${text}</span></label>`;
  shell(`<div class="hello"><span class="av">${ic('user', 28)}</span><h1>Привет${u.guest || op ? '' : ', ' + esc(u.name.split(' ')[0])}!</h1><img src="${mimg('happy')}" alt=""></div>
  ${op ? '<p class="muted">Вы вошли как оператор.</p>' : `<div class="menu y"><div>${ic('gift')}<span class="g">Бонусы</span><span class="chip w">Скоро</span></div></div>
  <div class="menu"><a href="#/orders">${ic('box')}<span class="g">Мои заказы</span>${ic('next', 20)}</a><a href="#/orders?f=done">${ic('bag')}<span class="g">Мои покупки</span>${ic('next', 20)}</a><a href="#/projects">${ic('folder')}<span class="g">Мои проекты</span>${ic('next', 20)}</a><div style="color:var(--muted)">${ic('heart')}<span class="g">Избранное</span><span class="chip">Скоро</span></div><button data-act="feedback">${ic('chat')}<span class="g">Отзывы и вопросы</span>${ic('next', 20)}</button></div>
  ${last ? `<h2>${last.status === 'completed' ? 'Прошлая покупка' : 'Последний заказ'}</h2><div class="card row"><a href="#/order/${last.id}">${book(last.cover, last.project_title, true)}</a><div class="g"><button class="btn sm" style="width:100%" data-act="reorder">Заказать ещё</button><a class="btn sm line" style="width:100%;margin-top:8px" href="#/order/${last.id}?review=1">Оставить отзыв</a></div></div>` : ''}`}
  <h2>Оформление</h2><div class="seg">${[['v3', 'Новое'], ['v2', 'Прошлое'], ['classic', 'Первое']].map(([v, l]) => `<button data-act="design" data-v="${v}" class="${(window.ppDesign ? window.ppDesign.get() : 'classic') === v ? 'on' : ''}">${l}</button>`).join('')}</div>
  ${V3 ? '<button class="rowbtn" data-act="onboard" style="margin-top:10px">' + ic('info') + '<span class="g">Как пользоваться приложением</span>' + ic('next', 20) + '</button>' : ''}
  <h2>Режим демонстрации</h2><div class="card">${check('off', state.sim.offline, 'Имитировать отсутствие сети: приложение не зависнет, черновик сохранится')}${op ? '' : check('lose', state.sim.loseResponse, 'Оборвать связь в момент оформления следующего заказа: дубликат не появится')}</div>
${u.guest ? '<h2>Сохраните свои работы</h2><div class="card"><p class="muted" style="margin:0 0 12px">Вы в гостевом режиме: проекты хранятся только в этом браузере. Создайте аккаунт или войдите, и проекты с заказами перейдут в него.</p><div class="stack"><button class="btn" data-act="account" data-mode="register">Создать аккаунт</button><button class="btn line" data-act="account" data-mode="login">Войти в существующий</button></div></div>' : ''}
  <h2>Аккаунт</h2><div class="stack"><button class="btn line" data-act="logout">${ic('out')}Выйти</button>
  ${op ? '' : '<button class="btn danger" data-act="wipe">Удалить мои данные и аккаунт</button><p class="why">Удалим фото, проекты, заказы и обращения без возможности восстановления</p>'}</div>`, { active: '/profile' });
  $('#off').onchange = (e) => { state.sim.offline = e.target.checked; toast(e.target.checked ? 'Сеть отключена (имитация)' : 'Сеть включена'); };
  const lose = $('#lose'); if (lose) lose.onchange = (e) => { state.sim.loseResponse = e.target.checked; };
  state.ctx = {
    reorder: (el) => reorder(last.project_id, el),
    feedback: () => (last ? go(`/order/${last.id}?review=1`) : toast('Отзыв можно оставить после первого заказа')),
    logout: async () => { try { await api('/auth/logout', { method: 'POST' }); } catch { /* ignore */ } state.token = null; state.user = null; store.del('pp_token'); go('/login'); },
    wipe: async () => { if (!confirm('Удалить аккаунт и все данные? Это нельзя отменить.')) return; try { await api('/me/data', { method: 'DELETE' }); state.token = null; state.user = null; store.del('pp_token'); toast('Данные удалены'); go('/login'); } catch (x) { toast(x.message); } },
  };
}

/* ---------- панель оператора ---------- */
const OP_TABS = [['orders', 'Заказы'], ['tickets', 'Обращения'], ['audit', 'Журнал'], ['funnel', 'Воронка']];
async function viewOp(tab = 'orders') {
  if (state.user.role !== 'operator') return go('/');
  const tabs = `<div class="tabs">${OP_TABS.map(([k, t]) => `<a href="#/op/${k}" class="${k === tab ? 'on' : ''}">${t}</a>`).join('')}</div>`;
  let body = '';
  if (tab === 'orders') {
    const orders = await api('/admin/orders');
    body = orders.length ? `<div class="stack">${orders.map((o) => `<div class="card"><div class="row"><span class="g"><h3>${esc(o.number)} · ${esc(o.client)}</h3><span class="muted">${esc(o.product_title)} · ${o.photos_count} фото · ${byn(o.total)} · ${fmt(o.created_at)}</span></span><span class="chip ${chipClass(o.status)}">${esc(LABELS[o.status] || o.status_label)}</span></div>${o.next.length ? `<div class="opts" style="margin-top:12px">${o.next.map((n) => `<button class="btn sm ${n === 'rejected' ? 'danger' : ''}" data-act="st" data-id="${o.id}" data-to="${n}">${n === 'rejected' ? 'Отклонить…' : '→ ' + LABELS[n]}</button>`).join('')}</div>` : ''}</div>`).join('')}</div>` : '<p class="muted">Заказов пока нет. Создайте заказ под клиентом.</p>';
  } else if (tab === 'tickets') {
    const t = await api('/admin/tickets');
    body = t.length ? `<div class="stack">${t.map((x) => `<div class="card"><span class="muted">Заказ ${x.order_id} · ${fmt(x.created_at)}</span><br>${esc(x.text)}</div>`).join('')}</div>` : '<p class="muted">Обращений нет.</p>';
  } else if (tab === 'audit') {
    const a = await api('/admin/audit');
    body = `<table class="log"><tr><th>Время</th><th>Роль</th><th>Действие</th><th>Объект</th><th>Итог</th></tr>${a.map((r) => `<tr><td>${fmt(r.ts)}</td><td>${esc(r.role || '—')}</td><td>${esc(r.action)}</td><td>${esc(r.object || '')}</td><td style="color:var(--${r.result === 'ok' ? 'ok' : r.result === 'replay' ? 'warn' : 'bad'})">${esc(r.result)}</td></tr>`).join('')}</table>`;
  } else {
    const f = await api('/admin/funnel'); const top = Math.max(1, f[0].users);
    body = `<p class="muted">Где пользователи уходят на пути к заказу.</p>${f.map((s, i) => `<div class="fun"><div class="row"><span>${esc(s.label)}</span><b>${s.users}${i ? ` · ${f[i - 1].users ? Math.round((s.users / f[i - 1].users) * 100) : 0}%` : ''}</b></div><div class="bar"><i style="width:${(s.users / top) * 100}%"></i></div></div>`).join('')}`;
  }
  shell(`${head('Панель оператора')}${tabs}${body}`, { active: '/op/' + tab });
  state.ctx = { st: async (el) => {
    let note = '';
    if (el.dataset.to === 'rejected') { note = prompt('Причина отклонения (её увидит клиент):') || ''; if (!note.trim()) return; }
    try { await api(`/admin/orders/${el.dataset.id}/status`, { method: 'POST', body: { to: el.dataset.to, note } }); await viewOp('orders'); } catch (x) { toast(x.message); }
  } };
}

/* ---------- маршрутизация ---------- */
async function route() {
  clearInterval(state.poll); closeSheet(true); state.backFn = null;
  const raw = location.hash.replace(/^#/, '') || '/';
  const [pathPart, qs] = raw.split('?');
  state.q = new URLSearchParams(qs || '');
  const [a, b, c] = pathPart.split('/').filter(Boolean);
  if (!state.user) {
    if (state.token) { try { state.user = await api('/me'); } catch { /* токен недействителен */ } }
    if (!state.user && a !== 'login') return go('/login');
  }
  if (state.user && a === 'login') return go('/');
  try {
    if (state.user && !state.capsLoaded) { if (state.capsReq) await state.capsReq; if (!state.capsLoaded) { try { state.caps = await api('/capabilities'); state.capsLoaded = true; } catch { /* необязательно */ } } }
    if (a === 'login') await viewLogin();
    else if (!a) await (state.user.role === 'operator' ? viewOp('orders') : viewHome());
    else if (a === 'new' || a === 'catalog' || a === 'product') await viewNew();
    else if (a === 'projects') await viewProjects();
    else if (a === 'project') await viewProject(b, c || 'photos');
    else if (a === 'cart') {
      const ps = await api('/projects'); const d = ps.find((x) => x.status === 'draft' && x.photos_count >= x.min_photos);
      if (d) go(`/project/${d.id}/cart`); else { toast('Корзина пуста: сначала соберите проект'); go(ps.find((x) => x.status === 'draft') ? '/projects' : '/new'); }
    }
    else if (a === 'orders') await viewOrders();
    else if (a === 'order') await viewOrder(b);
    else if (a === 'profile') await viewProfile();
    else if (a === 'op') await viewOp(b || 'orders');
    else go('/');
  } catch (e) { failScreen(e); }
}

const ACTS = {
  reload: () => route(),
  closesheet: () => closeSheet(),
  onboard: () => maybeOnboarding(true),
  design: (el) => { if (window.ppDesign && window.ppDesign.get() !== el.dataset.v) window.ppDesign.set(el.dataset.v); },
  account: (el, ev) => { if (ev) ev.preventDefault(); accountSheet(el.dataset.mode || 'register'); },
  guest: async (el) => { el.disabled = true; try { await auth('/auth/guest', {}); } catch (e) { el.disabled = false; toast(e.message); } },
  fill: (el, ev) => { ev.preventDefault(); $('#lg').value = el.dataset.l; $('#pw').value = el.dataset.p; },
  hidetip: (el) => { state.hidden.add(el.dataset.k); const b = $('#tip-' + el.dataset.k); if (b) b.remove(); },
  aihome: async (el) => {
    el.disabled = true;
    try {
      const ps = await api('/projects'); const d = ps.find((x) => x.status === 'draft');
      const p = d || await api('/projects', { method: 'POST', body: { product_id: 'classic', title: 'Моя фотокнига' } });
      if (!d) track('project_created');
      go(`/project/${p.id}/ai?mode=create`);
    } catch (e) { el.disabled = false; toast(e.message); }
  },
};
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]'); if (!el || el.disabled) return;
  const fn = ACTS[el.dataset.act] || (state.ctx && state.ctx[el.dataset.act]);
  if (fn) fn(el, e);
});
window.addEventListener('hashchange', route);

(async function boot() {
  const capsReq = api('/capabilities').then((c) => { state.caps = c; state.capsLoaded = true; }).catch(() => {});  // параллельно со входом
  state.capsReq = capsReq;
  await loadTelegram();
  platform.ready();
  platform.onBack(() => (state.backFn ? state.backFn() : go(state.back || '/')));
  const initData = platform.initData();
  if (initData) {  // внутри Telegram всегда входим как пользователь Telegram, даже если раньше был гостем
    try { const r = await api('/auth/telegram', { method: 'POST', body: { init_data: initData } }); state.token = r.token; state.user = r.user; store.set('pp_token', r.token); if (r.home) state.home = { t: Date.now(), ...r.home }; } catch { /* вне Telegram или бот не настроен — работаем как в браузере */ }
  }
  route();
})();
})();
