"""
FastAPI-сервер Mini App. Импортирует игровую логику из bot.py,
патчит _broadcast чтобы пушить состояние боя в WebSocket-клиенты.
"""
import asyncio
import hashlib
import hmac
import json
import os
import random
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import bot as B

app = FastAPI(title="Battle Bot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_WEBAPP = os.path.join(os.path.dirname(__file__), "webapp")
app.mount("/webapp", StaticFiles(directory=_WEBAPP, html=True), name="webapp")

@app.get("/")
async def root():
    return FileResponse(os.path.join(_WEBAPP, "index.html"))

# ── WebSocket-клиенты боя: {sid: {user_id: ws}} ──────────────────────────────
_ws: dict[int, dict[int, WebSocket]] = {}

# ── Патч _broadcast: после Telegram-сообщений пушим JSON в WS ────────────────
_orig_broadcast = B._broadcast

async def _patched_broadcast(session, bot):
    await _orig_broadcast(session, bot)
    await _push_ws(session)

B._broadcast = _patched_broadcast


async def _push_ws(session):
    clients = _ws.get(session.id, {})
    if not clients:
        return
    data = _session_json(session)
    dead = []
    for uid, ws in list(clients.items()):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(uid)
    for uid in dead:
        clients.pop(uid, None)


# ── Auth ──────────────────────────────────────────────────────────────────────
def _parse_init_data(raw: str) -> dict:
    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    received = parsed.pop("hash", "")
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", B.BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(401, "Invalid initData")
    return json.loads(parsed.get("user", "{}"))


async def _auth(request: Request) -> dict:
    raw = request.headers.get("X-Init-Data", "")
    if not raw:
        raise HTTPException(401, "No initData")
    return _parse_init_data(raw)


# ── Serializers ───────────────────────────────────────────────────────────────
def _combatant_json(c: B.Combatant) -> dict:
    return {
        "player_name": c.player_name,
        "unit_name": c.unit_name,
        "base_hp": c.base_hp,
        "base_max_hp": c.base_max_hp,
        "dmg_min": c.base_dmg_min,
        "dmg_max": c.base_dmg_max,
        "items": [
            {
                "slot": it["slot"], "name": it["name"],
                "dmg_add": it["dmg_add"], "cur_hp": it["cur_hp"],
                "max_hp": it["max_hp"], "broken": it["broken"],
                "perks": it["perks"],
            }
            for it in c.items
        ],
        "unit_perks": c.unit_perks,
        "burns": [{"left": b["left"], "level": b["level"]} for b in c.burns],
        "freezes": [{"left": f["left"], "level": f["level"]} for f in c.freezes],
        "boost_stacks": c.boost_stacks,
        "boom_markers": c.boom_markers,
        "last_hp_loss": c.last_hp_loss,
        "surrendered": c.surrendered,
    }


def _session_json(session) -> dict:
    b = session.battle
    return {
        "sid": session.id,
        "turn": b.turn,
        "finished": b.finished,
        "winner": b.winner,
        "events": b.events,
        "c1": _combatant_json(b.c1),
        "c2": _combatant_json(b.c2),
    }


def _find_user_session(user_id: int):
    for sid, session in B._sessions.items():
        for side, info in session.sides.items():
            if info["user_id"] == user_id:
                return session, side
    return None, None


# ── Player ────────────────────────────────────────────────────────────────────
@app.get("/api/me")
async def get_me(request: Request):
    user = await _auth(request)
    uid = user["id"]
    player = await B.get_or_create_player(uid, user.get("username") or user.get("first_name", ""))
    cat = B.category_of(player["cups"])
    amounts = {r["currency_id"]: r["amount"] for r in await B.list_player_currencies(uid)}
    currencies = []
    for c in await B.list_currencies():
        currencies.append({"id": c["id"], "icon": c["icon"], "amount": amounts.get(c["id"], 0)})
    now = int(time.time())
    events = [
        {
            "etype": ev["etype"],
            "label": B.EVENT_TYPES.get(ev["etype"], ev["etype"]),
            "multiplier": ev["multiplier"],
            "mins_left": (ev["end_time"] - now) // 60,
        }
        for ev in B._ACTIVE_EVENTS if ev["end_time"] > now
    ]
    return {
        "user_id": uid,
        "username": player["username"] or user.get("first_name", ""),
        "cups": player["cups"],
        "category": cat,
        "rank_mult": B.rank_multiplier(player["cups"]),
        "coins": player["coins"],
        "currencies": currencies,
        "donations": await B.get_player_donations(uid),
        "is_admin": B.is_admin(uid),
        "equipped_pu": player["equipped_pu"],
        "events": events,
    }


# ── Inventory ─────────────────────────────────────────────────────────────────
@app.get("/api/inventory")
async def get_inventory(request: Request):
    user = await _auth(request)
    uid = user["id"]
    player = await B.get_player(uid)
    if not player:
        raise HTTPException(404, "Player not found")

    units = []
    for pu in await B.list_player_units(uid):
        rar = await B.get_rarity(pu["rarity_id"]) if pu["rarity_id"] else None
        equipped_items = await B.items_on_unit(pu["pu_id"])
        items_list = []
        for it in equipped_items:
            irar = await B.get_rarity(it["rarity_id"]) if it["rarity_id"] else None
            items_list.append({
                "pi_id": it["pi_id"], "name": it["name"], "slot": it["slot"],
                "dmg_add": it["dmg_add"], "hp_add": it["hp_add"],
                "rarity_icon": irar["icon"] if irar else "",
                "perks": json.loads(it["perks"] or "[]"),
            })
        raw_items = [{"hp_add": it["hp_add"], "dmg_add": it["dmg_add"], "perks": it["perks"]}
                     for it in equipped_items]
        units.append({
            "pu_id": pu["pu_id"],
            "name": pu["name"],
            "rarity_icon": rar["icon"] if rar else "",
            "dmg_min": pu["dmg_min"], "dmg_max": pu["dmg_max"], "hp": pu["hp"],
            "perks": json.loads(pu["perks"] or "[]"),
            "power": B.calc_power(pu, raw_items),
            "equipped": pu["pu_id"] == player["equipped_pu"],
            "items": items_list,
        })

    all_items = []
    for it in await B.list_player_items(uid):
        irar = await B.get_rarity(it["rarity_id"]) if it["rarity_id"] else None
        all_items.append({
            "pi_id": it["pi_id"], "name": it["name"], "slot": it["slot"],
            "dmg_add": it["dmg_add"], "hp_add": it["hp_add"],
            "rarity_icon": irar["icon"] if irar else "",
            "perks": json.loads(it["perks"] or "[]"),
            "equipped_pu": it["equipped_pu"],
        })

    return {"units": units, "items": all_items}


@app.post("/api/equip-unit")
async def equip_unit(request: Request):
    user = await _auth(request)
    uid = user["id"]
    body = await request.json()
    pu_id = body.get("pu_id")
    units = await B.list_player_units(uid)
    if not any(u["pu_id"] == pu_id for u in units):
        raise HTTPException(403, "Not your unit")
    await B.set_equipped(uid, pu_id)
    return {"ok": True}


@app.post("/api/equip-item")
async def equip_item(request: Request):
    user = await _auth(request)
    uid = user["id"]
    body = await request.json()
    pi_id = body.get("pi_id")
    pu_id = body.get("pu_id")  # None = unequip
    items = await B.list_player_items(uid)
    if not any(it["pi_id"] == pi_id for it in items):
        raise HTTPException(403, "Not your item")
    await B.set_item_equipped(pi_id, pu_id)
    return {"ok": True}


# ── Summons ───────────────────────────────────────────────────────────────────
@app.get("/api/summons")
async def get_summons(request: Request):
    user = await _auth(request)
    uid = user["id"]
    result = []
    for s in await B.list_summons("summon"):
        if s["currency_id"] == B.COIN_CURRENCY_ID:
            icon = B.COIN_ICON
        else:
            c = await B.get_currency(s["currency_id"])
            icon = c["icon"] if c else "?"
        balance = await B.get_player_currency_amount(uid, s["currency_id"])
        pool = await B.build_pool(s)
        result.append({
            "id": s["id"], "name": s["name"],
            "price": s["price"], "price_x10": s["price"] * B.SUMMON_X10_DISCOUNT,
            "currency_id": s["currency_id"], "currency_icon": icon,
            "balance": balance, "pool_size": len(pool),
        })
    return {"summons": result}


@app.post("/api/summon/{sid}/pull")
async def do_summon(sid: int, request: Request):
    user = await _auth(request)
    uid = user["id"]
    body = await request.json()
    count = int(body.get("count", 1))
    if count not in (1, 10):
        raise HTTPException(400, "count must be 1 or 10")

    s = await B.get_summon(sid)
    if not s:
        raise HTTPException(404, "Not found")
    pool = await B.build_pool(s)
    if not pool:
        raise HTTPException(400, "Empty pool")

    price = s["price"] * (B.SUMMON_X10_DISCOUNT if count == 10 else 1)
    if not await B.spend_currency(uid, s["currency_id"], price):
        raise HTTPException(400, "Not enough currency")

    luck = await B.get_luck_mult(uid)
    results = []
    for _ in range(count):
        entry = B.roll_summon(pool, luck)
        if not entry:
            continue
        if entry["kind"] == "unit":
            pid = await B.add_player_unit(uid, entry["id"])
            results.append({"kind": "unit", "name": entry["name"],
                            "rarity_icon": entry["rarity_icon"], "id": pid})
        else:
            pid = await B.add_player_item(uid, entry["id"])
            results.append({"kind": "item", "name": entry["name"],
                            "rarity_icon": entry["rarity_icon"], "id": pid})
    return {"results": results}


# ── Crates ────────────────────────────────────────────────────────────────────
@app.get("/api/crates")
async def get_crates(request: Request):
    user = await _auth(request)
    uid = user["id"]
    result = []
    for s in await B.list_summons("crate"):
        if s["currency_id"] == B.COIN_CURRENCY_ID:
            icon = B.COIN_ICON
        else:
            c = await B.get_currency(s["currency_id"])
            icon = c["icon"] if c else "?"
        result.append({
            "id": s["id"], "name": s["name"],
            "price": s["price"],
            "currency_id": s["currency_id"], "currency_icon": icon,
            "balance": await B.get_player_currency_amount(uid, s["currency_id"]),
        })
    return {"crates": result}


@app.post("/api/crate/{cid}/open")
async def open_crate(cid: int, request: Request):
    user = await _auth(request)
    uid = user["id"]
    s = await B.get_summon(cid)
    if not s or s["kind"] != "crate":
        raise HTTPException(404, "Not found")
    if not await B.spend_currency(uid, s["currency_id"], s["price"]):
        raise HTTPException(400, "Not enough currency")
    pool = await B.build_pool(s)
    if not pool:
        raise HTTPException(400, "Empty pool")
    luck = await B.get_luck_mult(uid)
    entry = B.roll_summon(pool, luck)
    if not entry:
        raise HTTPException(500, "Roll failed")
    if entry["kind"] == "unit":
        pid = await B.add_player_unit(uid, entry["id"])
    else:
        pid = await B.add_player_item(uid, entry["id"])
    return {"kind": entry["kind"], "name": entry["name"],
            "rarity_icon": entry["rarity_icon"], "id": pid}


# ── Leaderboard ───────────────────────────────────────────────────────────────
@app.get("/api/leaderboard")
async def get_leaderboard(request: Request):
    await _auth(request)
    rows = await B.top_players(10)
    return {
        "players": [
            {"username": B.display_name(p), "cups": p["cups"],
             "category": B.category_of(p["cups"])}
            for p in rows
        ]
    }


# ── Clan ──────────────────────────────────────────────────────────────────────
@app.get("/api/clan")
async def get_my_clan(request: Request):
    user = await _auth(request)
    uid = user["id"]
    clan = await B.get_player_clan(uid)
    if not clan:
        return {"clan": None}
    members = await B.clan_members_list(clan["id"])
    return {
        "clan": {
            "id": clan["id"], "name": clan["name"],
            "description": clan["description"],
            "owner_id": clan["owner_id"], "entry_mode": clan["entry_mode"],
            "members": [
                {"user_id": m["user_id"],
                 "username": m["username"] or f"id{m['user_id']}"}
                for m in members
            ],
        }
    }


@app.get("/api/clans")
async def list_clans(request: Request):
    await _auth(request)
    result = []
    for c in await B.list_clans():
        result.append({
            "id": c["id"], "name": c["name"],
            "description": c["description"],
            "member_count": await B.clan_member_count(c["id"]),
            "entry_mode": c["entry_mode"],
        })
    return {"clans": result}


# ── Battle ────────────────────────────────────────────────────────────────────
@app.post("/api/battle/bot")
async def start_bot_battle(request: Request):
    user = await _auth(request)
    uid = user["id"]
    body = await request.json()
    difficulty = body.get("difficulty", "medium")
    if difficulty not in B.BOT_DIFF_INFO:
        raise HTTPException(400, "Bad difficulty")
    if any(q["user_id"] == uid for q in B._queue) or B._in_battle(uid):
        raise HTTPException(400, "Already in battle or queue")

    pu_id = await B._ensure_equipped(uid)
    if not pu_id:
        raise HTTPException(400, "No unit equipped. Get one via Summon first!")

    player = await B.get_player(uid)
    unit = await B.build_battle_unit(pu_id)
    c1 = B.Combatant(1, B.display_name(player), unit, is_bot_battle=True)
    c2 = await B.make_bot_opponent(pu_id, difficulty=difficulty)
    if not c2:
        raise HTTPException(400, "No suitable opponent bot found")

    sides = {
        1: {
            "user_id": uid, "chat_id": None, "msg_id": None,
            "pre_cups": player["cups"],
            "diff_mult": B.BOT_DIFF_INFO[difficulty]["reward_mult"],
            "_webapp": True,
        }
    }
    session = await _start_webapp_session(B.Battle(c1, c2, is_bot=True), sides)
    return {"sid": session.id, "state": _session_json(session)}


@app.post("/api/battle/{sid}/surrender")
async def surrender(sid: int, request: Request):
    user = await _auth(request)
    uid = user["id"]
    session = B._sessions.get(sid)
    if not session:
        raise HTTPException(404, "Session not found")
    side = next((s for s, i in session.sides.items() if i["user_id"] == uid), None)
    if side is None:
        raise HTTPException(403, "Not your battle")
    session.battle.surrender(side)
    await _push_ws(session)
    if session.task:
        session.task.cancel()
    asyncio.create_task(_end_webapp_session(session))
    return {"ok": True}


@app.get("/api/battle/{sid}/state")
async def get_battle_state(sid: int, request: Request):
    user = await _auth(request)
    uid = user["id"]
    session = B._sessions.get(sid)
    if not session:
        raise HTTPException(404, "Session not found")
    if not any(i["user_id"] == uid for i in session.sides.values()):
        raise HTTPException(403, "Not your battle")
    return _session_json(session)


@app.websocket("/ws/battle/{sid}")
async def ws_battle(websocket: WebSocket, sid: int):
    await websocket.accept()
    raw = websocket.query_params.get("initData", "")
    try:
        user = _parse_init_data(raw)
    except HTTPException:
        await websocket.close(code=4001)
        return

    uid = user["id"]
    session = B._sessions.get(sid)
    if not session or not any(i["user_id"] == uid for i in session.sides.values()):
        await websocket.close(code=4003)
        return

    _ws.setdefault(sid, {})[uid] = websocket
    # Сразу шлём текущее состояние
    await websocket.send_json(_session_json(session))

    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _ws.get(sid, {}).pop(uid, None)


# ── Webapp battle session (без Telegram-сообщений) ───────────────────────────
async def _start_webapp_session(battle: B.Battle, sides: dict) -> B.Session:
    import bot
    sid = bot._next_sid
    bot._next_sid += 1
    session = B.Session(sid, battle, sides, is_bot=True)
    bot._sessions[sid] = session
    session.task = asyncio.create_task(_webapp_loop(session))
    return session


async def _webapp_loop(session: B.Session):
    try:
        while not session.battle.finished:
            await asyncio.sleep(B.TURN_DELAY)
            if session.done or session.battle.finished:
                break
            session.battle.step()
            await _push_ws(session)
    except asyncio.CancelledError:
        return
    await _end_webapp_session(session)


async def _end_webapp_session(session: B.Session):
    if session.done:
        return
    session.done = True
    B._sessions.pop(session.id, None)

    b = session.battle
    result_by_uid = {}

    for side, info in session.sides.items():
        uid = info["user_id"]
        won = b.winner == side
        draw = b.winner == 0
        pre_cups = info["pre_cups"]
        diff_mult = info.get("diff_mult", 1.0)
        don_mult = await B.get_coin_mult(uid)

        if draw:
            coins = round(random.randint(*B.COIN_REWARD_WIN) * B.get_event_mult("earn") * don_mult)
            await B.give_currency(uid, B.COIN_CURRENCY_ID, coins)
            extras = await B.grant_battle_currencies(uid, True, pre_cups, don_mult)
            result_by_uid[uid] = {
                "outcome": "draw", "cups": 0, "coins": coins,
                "extras": [{"icon": ic, "amount": a} for ic, a in extras],
            }
        else:
            cat = B.category_of(pre_cups)
            delta = (
                max(1, round(B.cup_reward(cat, True) * diff_mult * B.get_event_mult("wins")))
                if won else B.cup_reward(cat, False)
            )
            await B.add_cups(uid, delta)
            coins = round(
                random.randint(*(B.COIN_REWARD_WIN if won else B.COIN_REWARD_LOSS))
                * B.get_event_mult("earn") * don_mult
            )
            await B.give_currency(uid, B.COIN_CURRENCY_ID, coins)
            extras = await B.grant_battle_currencies(uid, won, pre_cups, don_mult)
            result_by_uid[uid] = {
                "outcome": "win" if won else "loss",
                "cups": delta, "coins": coins,
                "extras": [{"icon": ic, "amount": a} for ic, a in extras],
            }

    # финальный push с результатом
    clients = _ws.pop(session.id, {})
    final = _session_json(session)
    for uid, ws in clients.items():
        final["result"] = result_by_uid.get(uid, {})
        try:
            await ws.send_json(final)
        except Exception:
            pass
