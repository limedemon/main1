// ── Config ────────────────────────────────────────────────────────────────────
// Замени на URL своего сервиса на Render.com после деплоя
const API_BASE = window.location.origin;

const TG = window.Telegram.WebApp;
TG.ready();
TG.expand();
TG.setHeaderColor('#0a0a18');
TG.setBackgroundColor('#0a0a18');

// ── State ─────────────────────────────────────────────────────────────────────
const S = {
  player: null,
  inventory: null,
  summons: null,
  crates: null,
  leaderboard: null,
  clan: null,
  battle: { sid: null, state: null, ws: null, result: null },
  invTab: 'units',
};

// ── API helper ────────────────────────────────────────────────────────────────
async function api(method, path, body = null) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Init-Data': TG.initData || '',
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}`);
  }
  return res.json();
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

// ── Screens ───────────────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById('screen-' + name);
  if (el) el.classList.add('active');

  const withNav = ['menu', 'summon', 'crates', 'inventory', 'leaderboard', 'clan'];
  document.getElementById('bottom-nav').style.display =
    withNav.includes(name) ? 'flex' : 'none';

  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const navEl = document.querySelector(`.nav-item[data-screen="${name}"]`);
  if (navEl) navEl.classList.add('active');
}

// ── Main menu ─────────────────────────────────────────────────────────────────
async function loadMenu() {
  try {
    S.player = await api('GET', '/api/me');
    renderMenu();
    showScreen('menu');
  } catch (e) {
    toast('Ошибка загрузки: ' + e.message);
  }
}

function renderMenu() {
  const p = S.player;
  document.getElementById('stat-cups').textContent = p.cups;
  document.getElementById('stat-coins').textContent = p.coins;
  document.getElementById('stat-rank').textContent = `⚜️ Ранг ${p.category}`;
  document.getElementById('stat-mult').textContent = `×${fmtMult(p.rank_mult)} к наградам`;

  const extras = document.getElementById('stat-extras');
  extras.innerHTML = '';
  for (const c of p.currencies) {
    const d = document.createElement('div');
    d.className = 'stat-pill';
    d.innerHTML = `<span style="font-size:20px">${c.icon}</span>
      <div><div class="val">${c.amount}</div><div class="lbl">валюта</div></div>`;
    extras.appendChild(d);
  }

  const evBox = document.getElementById('menu-events');
  evBox.innerHTML = '';
  for (const ev of (p.events || [])) {
    const d = document.createElement('div');
    d.className = 'event-chip';
    d.innerHTML = `<span>${ev.label} ×${fmtMult(ev.multiplier)}</span>
      <span class="emins">${ev.mins_left} мин</span>`;
    evBox.appendChild(d);
  }
}

// ── Battle ────────────────────────────────────────────────────────────────────
function showBattleSelect() {
  showScreen('battle-select');
}

async function startBotBattle(difficulty) {
  showScreen('battle');
  renderBattleLoading();
  try {
    const data = await api('POST', '/api/battle/bot', { difficulty });
    S.battle.sid = data.sid;
    S.battle.state = data.state;
    S.battle.result = null;
    connectBattleWS(data.sid);
    renderBattle(data.state);
  } catch (e) {
    toast(e.message);
    showScreen('menu');
  }
}

function connectBattleWS(sid) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const host = location.host;
  const initData = encodeURIComponent(TG.initData || '');
  const ws = new WebSocket(`${proto}://${host}/ws/battle/${sid}?initData=${initData}`);
  S.battle.ws = ws;

  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    S.battle.state = data;
    renderBattle(data);
    if (data.finished) {
      if (data.result) {
        // финальное сообщение с наградами — закрываем и показываем результат
        ws.close();
        showResult(data.result);
      }
      // иначе ждём следующее сообщение с result (придёт через ~мс)
    }
  };

  ws.onclose = () => {
    S.battle.ws = null;
    // Если бой закончился но result ещё не показан — поллим за результатом
    if (S.battle.sid && !S.battle.result) {
      setTimeout(() => pollBattleResult(sid), 500);
    }
  };

  // keepalive ping
  const ping = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) ws.send('ping');
    else clearInterval(ping);
  }, 25000);
}

async function pollBattleState(sid) {
  if (!S.battle.sid) return;
  try {
    const data = await api('GET', `/api/battle/${sid}/state`);
    S.battle.state = data;
    renderBattle(data);
    if (data.finished) {
      if (data.result) showResult(data.result);
    } else {
      setTimeout(() => pollBattleState(sid), 4500);
    }
  } catch (_) {}
}

// Фолбэк: если WS закрылся раньше чем пришёл result — поллим пока сессия жива
async function pollBattleResult(sid) {
  if (S.battle.result) return;
  try {
    const data = await api('GET', `/api/battle/${sid}/state`);
    renderBattle(data);
    if (data.result) {
      showResult(data.result);
    } else if (!data.finished) {
      // бой ещё идёт — переподключаемся
      connectBattleWS(sid);
    } else {
      // finished но result ещё не готов — ждём чуть-чуть
      setTimeout(() => pollBattleResult(sid), 800);
    }
  } catch (_) {
    // сессия уже удалена (result был отправлен, но мы его пропустили)
    // показываем экран "бой завершён" без деталей
    document.getElementById('result-overlay').classList.add('show');
    document.getElementById('result-icon').textContent = '⚔️';
    document.getElementById('result-title').textContent = 'Бой завершён';
    document.getElementById('result-title').className = 'result-title draw';
    document.getElementById('result-rewards').innerHTML = '';
  }
}

function renderBattleLoading() {
  document.getElementById('battle-turn').textContent = 'Загрузка...';
  document.getElementById('battle-log-lines').innerHTML = '';
}

function renderBattle(st) {
  if (!st) return;
  document.getElementById('battle-turn').textContent = `Ход ${st.turn}`;

  renderCombatant('c2', st.c2, false); // противник сверху
  renderCombatant('c1', st.c1, true);  // игрок снизу

  const log = document.getElementById('battle-log-lines');
  log.innerHTML = (st.events || []).map(l => `<div class="log-line">${escHtml(l)}</div>`).join('');
  const logBox = document.getElementById('battle-log');
  logBox.scrollTop = logBox.scrollHeight;
}

function renderCombatant(id, c, isPlayer) {
  const prev = window._prevHp || {};
  const prevHp = prev[id];

  const hpPct = c.base_max_hp > 0 ? Math.max(0, c.base_hp / c.base_max_hp * 100) : 0;
  document.getElementById(`${id}-name`).textContent = isPlayer ? `👤 ${c.player_name}` : `🤖 ${c.player_name}`;
  document.getElementById(`${id}-unit`).textContent = c.unit_name;
  document.getElementById(`${id}-hp-bar`).style.width = hpPct + '%';
  document.getElementById(`${id}-hp-txt`).textContent = `${c.base_hp} / ${c.base_max_hp}`;
  document.getElementById(`${id}-dmg`).textContent = `⚔️ ${c.dmg_min}–${c.dmg_max}`;

  // shake on hit
  if (prevHp !== undefined && c.base_hp < prevHp) {
    const card = document.getElementById(`${id}-card`);
    card.classList.remove('hit');
    void card.offsetWidth;
    card.classList.add('hit');
    setTimeout(() => card.classList.remove('hit'), 400);
  }
  if (!window._prevHp) window._prevHp = {};
  window._prevHp[id] = c.base_hp;

  // items
  const itemsEl = document.getElementById(`${id}-items`);
  itemsEl.innerHTML = (c.items || []).map(it => {
    const cls = it.broken ? 'item-chip broken' : 'item-chip';
    const hp = it.max_hp > 0 ? ` ❤️${it.cur_hp}` : '';
    return `<span class="${cls}">${it.slot}${escHtml(it.name)} ⚔️+${it.dmg_add}${hp}</span>`;
  }).join('');

  // effects
  const efx = document.getElementById(`${id}-effects`);
  const marks = [];
  for (const b of (c.burns || [])) marks.push(`<span class="effect-badge fire">🔥×${b.level}</span>`);
  for (const f of (c.freezes || [])) marks.push(`<span class="effect-badge freeze">❄️×${f.level}</span>`);
  for (const b of (c.boost_stacks || [])) marks.push(`<span class="effect-badge boost">⚡️${b}</span>`);
  for (const b of (c.boom_markers || [])) marks.push(`<span class="effect-badge boom">💥${b}</span>`);
  efx.innerHTML = marks.join('');
}

async function surrenderBattle() {
  if (!S.battle.sid) return;
  if (!confirm('Сдаться?')) return;
  try {
    await api('POST', `/api/battle/${S.battle.sid}/surrender`);
  } catch (e) {
    toast(e.message);
  }
}

function showResult(result) {
  S.battle.result = result;
  S.battle.sid = null;

  const overlay = document.getElementById('result-overlay');
  const outcome = result.outcome;

  const icons = { win: '🏆', loss: '💀', draw: '🤝' };
  const titles = { win: 'Победа!', loss: 'Поражение', draw: 'Ничья' };

  document.getElementById('result-icon').textContent = icons[outcome] || '⚔️';
  const titleEl = document.getElementById('result-title');
  titleEl.textContent = titles[outcome] || '';
  titleEl.className = 'result-title ' + outcome;

  const rewards = document.getElementById('result-rewards');
  rewards.innerHTML = '';
  const sign = result.cups >= 0 ? '+' : '';
  rewards.innerHTML += `<div class="reward-row">${sign}${result.cups} 🏆</div>`;
  rewards.innerHTML += `<div class="reward-row">+${result.coins} 💰</div>`;
  for (const ex of (result.extras || [])) {
    rewards.innerHTML += `<div class="reward-row">+${ex.amount} ${ex.icon}</div>`;
  }

  overlay.classList.add('show');
}

function closeResult() {
  document.getElementById('result-overlay').classList.remove('show');
  loadMenu();
  showScreen('menu');
}

// ── Summon ────────────────────────────────────────────────────────────────────
async function loadSummons() {
  try {
    S.summons = await api('GET', '/api/summons');
    renderSummons();
    showScreen('summon');
  } catch (e) {
    toast(e.message);
  }
}

function renderSummons() {
  const list = document.getElementById('summon-list');
  list.innerHTML = '';
  const summons = S.summons?.summons || [];
  if (!summons.length) {
    list.innerHTML = '<div class="empty-state"><div class="ico">👤</div><p>Суммонов пока нет</p></div>';
    return;
  }
  for (const s of summons) {
    const card = document.createElement('div');
    card.className = 'summon-card card';
    const canX1 = s.balance >= s.price;
    const canX10 = s.balance >= s.price_x10;
    card.innerHTML = `
      <h3>${escHtml(s.name)}</h3>
      <div class="balance">${s.currency_icon} ${s.balance} / нужно ${s.price}</div>
      <div class="actions">
        <button class="btn btn-primary btn-sm" ${canX1 ? '' : 'disabled'}
          onclick="doSummon(${s.id},1,'${escHtml(s.currency_icon)}')">
          ×1 — ${s.price}${s.currency_icon}
        </button>
        <button class="btn btn-secondary btn-sm" ${canX10 ? '' : 'disabled'}
          onclick="doSummon(${s.id},10,'${escHtml(s.currency_icon)}')">
          ×10 — ${s.price_x10}${s.currency_icon}
        </button>
      </div>`;
    list.appendChild(card);
  }
}

async function doSummon(sid, count, icon) {
  try {
    const data = await api('POST', `/api/summon/${sid}/pull`, { count });
    showGacha(data.results, count);
    S.summons = await api('GET', '/api/summons');
    renderSummons();
  } catch (e) {
    toast(e.message);
  }
}

function showGacha(results, count) {
  const overlay = document.getElementById('gacha-overlay');
  overlay.classList.add('show');

  if (count === 1 && results.length === 1) {
    const r = results[0];
    overlay.innerHTML = `
      <div class="gacha-card-anim">
        <div class="gacha-rarity">${r.rarity_icon}</div>
        <div class="gacha-name">${escHtml(r.name)}</div>
        <div class="gacha-kind">${r.kind === 'unit' ? '👤 Юнит' : '🧩 Предмет'}</div>
      </div>
      <button class="btn btn-primary" onclick="closeGacha()">Забрать</button>`;
  } else {
    const grid = results.map(r =>
      `<div class="mini-gacha-card">
        <div class="mgr">${r.rarity_icon}</div>
        <div style="font-size:11px;margin-top:4px">${escHtml(r.name)}</div>
      </div>`
    ).join('');
    overlay.innerHTML = `
      <div style="font-size:18px;font-weight:700;margin-bottom:8px">×${results.length} результатов!</div>
      <div class="multi-gacha-grid">${grid}</div>
      <button class="btn btn-primary" onclick="closeGacha()" style="margin-top:12px">Забрать</button>`;
  }
}

function closeGacha() {
  document.getElementById('gacha-overlay').classList.remove('show');
}

// ── Crates ────────────────────────────────────────────────────────────────────
async function loadCrates() {
  try {
    S.crates = await api('GET', '/api/crates');
    renderCrates();
    showScreen('crates');
  } catch (e) {
    toast(e.message);
  }
}

function renderCrates() {
  const list = document.getElementById('crates-list');
  list.innerHTML = '';
  const crates = S.crates?.crates || [];
  if (!crates.length) {
    list.innerHTML = '<div class="empty-state"><div class="ico">🧳</div><p>Крейтов пока нет</p></div>';
    return;
  }
  for (const c of crates) {
    const card = document.createElement('div');
    card.className = 'summon-card card';
    const can = c.balance >= c.price;
    card.innerHTML = `
      <h3>🧳 ${escHtml(c.name)}</h3>
      <div class="balance">${c.currency_icon} ${c.balance} / нужно ${c.price}</div>
      <div class="actions">
        <button class="btn btn-primary ${can ? '' : 'disabled'}" ${can ? '' : 'disabled'}
          onclick="openCrate(${c.id})">
          Открыть — ${c.price}${c.currency_icon}
        </button>
      </div>`;
    list.appendChild(card);
  }
}

async function openCrate(cid) {
  try {
    const r = await api('POST', `/api/crate/${cid}/open`);
    showGacha([r], 1);
    S.crates = await api('GET', '/api/crates');
    renderCrates();
  } catch (e) {
    toast(e.message);
  }
}

// ── Inventory ─────────────────────────────────────────────────────────────────
async function loadInventory() {
  try {
    S.inventory = await api('GET', '/api/inventory');
    renderInventory();
    showScreen('inventory');
  } catch (e) {
    toast(e.message);
  }
}

function switchInvTab(tab) {
  S.invTab = tab;
  document.querySelectorAll('.inv-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.inv-tab[data-tab="${tab}"]`).classList.add('active');
  document.querySelectorAll('.inv-content').forEach(c => c.classList.remove('active'));
  document.getElementById('inv-' + tab).classList.add('active');
}

function renderInventory() {
  const { units, items } = S.inventory || { units: [], items: [] };
  const equippedPu = S.player?.equipped_pu;

  const unitsEl = document.getElementById('inv-units');
  unitsEl.innerHTML = '';
  if (!units.length) {
    unitsEl.innerHTML = '<div class="empty-state"><div class="ico">👤</div><p>Нет юнитов. Иди в Суммон!</p></div>';
  } else {
    for (const u of units) {
      const isEq = u.pu_id === equippedPu;
      const card = document.createElement('div');
      card.className = 'unit-card';
      const perksHtml = u.perks.map(p => `<span class="item-chip">${p}</span>`).join('');
      const itemsHtml = u.items.map(it =>
        `<span class="item-chip">${it.slot}${escHtml(it.name)}</span>`
      ).join('');
      card.innerHTML = `
        <div class="unit-card-header">
          <div>
            <div class="uname">${u.rarity_icon}${escHtml(u.name)}${u.rarity_icon}</div>
            ${isEq ? '<span style="font-size:11px;color:var(--green)">✓ Выбран</span>' : ''}
          </div>
          <div class="power">⚡ ${u.power}</div>
        </div>
        <div class="unit-card-stats">
          <span>⚔️ ${u.dmg_min}–${u.dmg_max}</span>
          <span>❤️ ${u.hp}</span>
        </div>
        ${perksHtml ? `<div class="unit-card-items">${perksHtml}</div>` : ''}
        ${itemsHtml ? `<div class="unit-card-items">${itemsHtml}</div>` : ''}
        <div class="unit-card-actions">
          ${!isEq ? `<button class="btn btn-primary btn-sm" onclick="equipUnit(${u.pu_id})">Выбрать</button>` : ''}
        </div>`;
      unitsEl.appendChild(card);
    }
  }

  const itemsEl = document.getElementById('inv-items');
  itemsEl.innerHTML = '';
  const unequipped = items.filter(it => !it.equipped_pu);
  if (!unequipped.length) {
    itemsEl.innerHTML = '<div class="empty-state"><div class="ico">🧩</div><p>Нет свободных предметов</p></div>';
  } else {
    for (const it of unequipped) {
      const row = document.createElement('div');
      row.className = 'item-row';
      row.innerHTML = `
        <div class="slot-ico">${it.slot}</div>
        <div class="iinfo">
          <div class="iname">${it.rarity_icon}${escHtml(it.name)}</div>
          <div class="istats">⚔️+${it.dmg_add} ❤️${it.hp_add}</div>
        </div>`;
      itemsEl.appendChild(row);
    }
  }

  switchInvTab(S.invTab);
}

async function equipUnit(puId) {
  try {
    await api('POST', '/api/equip-unit', { pu_id: puId });
    S.player = await api('GET', '/api/me');
    S.inventory = await api('GET', '/api/inventory');
    renderInventory();
    toast('Юнит выбран!');
  } catch (e) {
    toast(e.message);
  }
}

// ── Leaderboard ───────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  try {
    S.leaderboard = await api('GET', '/api/leaderboard');
    renderLeaderboard();
    showScreen('leaderboard');
  } catch (e) {
    toast(e.message);
  }
}

function renderLeaderboard() {
  const list = document.getElementById('lb-list');
  list.innerHTML = '';
  const players = S.leaderboard?.players || [];
  if (!players.length) {
    list.innerHTML = '<div class="empty-state"><div class="ico">🏆</div><p>Пока никого нет</p></div>';
    return;
  }
  const medals = ['🥇', '🥈', '🥉'];
  players.forEach((p, i) => {
    const row = document.createElement('div');
    const isMe = S.player && p.username === (S.player.username || '');
    row.className = 'lb-row' + (isMe ? ' me' : '');
    row.innerHTML = `
      <div class="lb-rank">${medals[i] || i + 1}</div>
      <div class="lb-name">${escHtml(p.username)}</div>
      <div>
        <div class="lb-cups">${p.cups} 🏆</div>
        <div class="lb-cat">Ранг ${p.category}</div>
      </div>`;
    list.appendChild(row);
  });
}

// ── Clan ──────────────────────────────────────────────────────────────────────
async function loadClan() {
  try {
    S.clan = await api('GET', '/api/clan');
    renderClan();
    showScreen('clan');
  } catch (e) {
    toast(e.message);
  }
}

function renderClan() {
  const clanBox = document.getElementById('clan-content');
  const clan = S.clan?.clan;

  if (!clan) {
    clanBox.innerHTML = `
      <div class="no-clan-box">
        <div class="ico">🛡</div>
        <p>Ты не в клане</p>
        <button class="btn btn-primary" onclick="loadClanList()">Найти клан</button>
      </div>`;
    return;
  }

  const members = clan.members || [];
  const membersHtml = members.map(m => {
    const isOwner = m.user_id === clan.owner_id;
    return `<div class="clan-member">${isOwner ? '👑 ' : ''}${escHtml(m.username)}</div>`;
  }).join('');

  clanBox.innerHTML = `
    <div class="clan-header-card">
      <h2>🛡 ${escHtml(clan.name)}</h2>
      <div class="cdesc">${escHtml(clan.description || 'Нет описания')}</div>
      <div style="font-size:13px">👥 ${members.length} / 20 участников</div>
    </div>
    <div style="padding:0 16px 8px;font-size:13px;font-weight:700;color:var(--sub)">УЧАСТНИКИ</div>
    <div class="clan-members-list">${membersHtml}</div>`;
}

async function loadClanList() {
  try {
    const data = await api('GET', '/api/clans');
    renderClanList(data.clans || []);
  } catch (e) {
    toast(e.message);
  }
}

function renderClanList(clans) {
  const clanBox = document.getElementById('clan-content');
  if (!clans.length) {
    clanBox.innerHTML = `<div class="empty-state"><div class="ico">🛡</div><p>Кланов пока нет</p></div>`;
    return;
  }
  const items = clans.map(c => `
    <div class="card" style="margin:0 16px 10px;padding:14px">
      <div style="font-size:16px;font-weight:700;margin-bottom:4px">🛡 ${escHtml(c.name)}</div>
      <div style="font-size:12px;color:var(--sub);margin-bottom:8px">${escHtml(c.description || '')}</div>
      <div style="font-size:12px;margin-bottom:10px">👥 ${c.member_count}/20 · ${c.entry_mode === 'open' ? '🔓 Открытый' : '🔒 По заявке'}</div>
    </div>`).join('');
  clanBox.innerHTML = `
    <div class="back-row" onclick="loadClan()">← Назад</div>
    <div style="padding-top:10px">${items}</div>`;
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function fmtMult(m) {
  return Number.isInteger(m) ? String(m) : m.toFixed(2).replace(/\.?0+$/, '');
}

function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  // Nav items
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const screen = el.dataset.screen;
      if (screen === 'summon') loadSummons();
      else if (screen === 'inventory') loadInventory();
      else if (screen === 'leaderboard') loadLeaderboard();
      else if (screen === 'clan') loadClan();
      else if (screen === 'crates') loadCrates();
      else showScreen(screen);
    });
  });

  await loadMenu();
});
