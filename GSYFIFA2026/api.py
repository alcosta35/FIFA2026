#!/usr/bin/env python3
"""
GSYFIFA2026 – Phase-by-Phase Betting Pool API
Phases: groups → r32 → r16 → qf → sf → final
Points accumulate across all phases.

Env vars:
  GSY_DATA_DIR  (default /opt/gsyfifa2026)
  GSY_HOST      (default 127.0.0.1)
  GSY_PORT      (default 5051)
  GSY_ADMIN_PIN (default gsy2026)

Endpoints:
  GET  /api/results          – current official results + phase state
  POST /api/results          – (admin) save results / phase state
  POST /api/bet              – submit a phase bet (one per player per phase; replaces previous)
  GET  /api/mybet            – look up own bets by email+phone
  GET  /api/scoreboard       – aggregate ranking across all phases
  GET  /api/score            – individual score breakdown
  GET  /api/health           – health check
"""
import json, os, threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

app      = Flask(__name__)
CORS(app)

DATA_DIR    = os.environ.get("GSY_DATA_DIR",  "/opt/gsyfifa2026")
BETS_FILE   = os.path.join(DATA_DIR, "bets.json")
RES_FILE    = os.path.join(DATA_DIR, "results.json")
PAYEES_FILE = os.path.join(DATA_DIR, "payees.json")
ADMIN_PIN   = os.environ.get("GSY_ADMIN_PIN", "gsy2026")

GK        = list("ABCDEFGHIJKL")
PHASES    = ["groups", "r32", "r16", "qf", "sf", "final"]
KO_PTS    = {"r32": 2, "r16": 3, "qf": 4, "sf": 5}

_lock = threading.Lock()


# ─────────────────────── helpers ────────────────────────
def _read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [] if default is None else default

def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def _norm_phone(p):
    return "".join(c for c in (p or "") if c.isdigit())


# ─────────────────────── scoring ────────────────────────
def _score_groups(bet, results):
    pts = 0
    gs  = results.get("groupStage") or {}
    for g in GK:
        grp = ((bet.get("groupScores") or {}).get(g)) or []
        for i in range(6):
            res  = gs.get(f"{g}_{i}")
            pred = grp[i] if i < len(grp) else {}
            if not res or not pred:
                continue
            rh, ra = res.get("h"), res.get("a")
            ph, pa = pred.get("h"), pred.get("a")
            if any(x is None or x == "" for x in [rh, ra, ph, pa]):
                continue
            rh, ra, ph, pa = int(rh), int(ra), int(ph), int(pa)
            if rh == ph and ra == pa:
                pts += 3
            elif (rh > ra) == (ph > pa) and (rh == ra) == (ph == pa):
                pts += 1
    return pts

def _score_ko(bet, phase, results):
    pts   = 0
    ppw   = KO_PTS[phase]
    picks = bet.get("picks") or {}
    for idx, team in (results.get(phase) or {}).items():
        if picks.get(str(idx)) == team:
            pts += ppw
    return pts

def _score_final(bet, results):
    pts           = 0
    picked_champ  = bet.get("champion") or ""
    picked_third  = bet.get("third")    or ""
    act_champ     = results.get("champion")  or ""
    act_runner    = results.get("runnerUp")  or ""
    act_third     = results.get("third")     or ""
    act_fourth    = results.get("fourth")    or ""
    if act_champ and picked_champ:
        if   picked_champ == act_champ:  pts += 10
        elif picked_champ == act_runner: pts += 7
    if act_third and picked_third:
        if   picked_third == act_third:  pts += 5
        elif picked_third == act_fourth: pts += 3
    return pts

def score_bet(bet, results):
    """Return (total_pts, detail_dict)."""
    phase = bet.get("phase") or "groups"
    det   = {p: 0 for p in ["groups", "r32", "r16", "qf", "sf", "final"]}
    if phase == "groups":
        p = _score_groups(bet, results)
        det["groups"] = p
    elif phase in KO_PTS:
        p = _score_ko(bet, phase, results)
        det[phase] = p
    elif phase == "final":
        p = _score_final(bet, results)
        det["final"] = p
    else:
        p = 0
    return p, det


def _aggregate_players(bets, results):
    """Merge all phase bets per player into a single ranked row."""
    players = {}   # email → row
    for bet in bets:
        pl    = bet.get("player") or {}
        email = pl.get("email", "").lower().strip()
        name  = pl.get("name",  "")
        phase = bet.get("phase") or "groups"
        if not email:
            continue
        pts, det = score_bet(bet, results)
        if email not in players:
            players[email] = {
                "name": name, "email": email,
                "score": 0, "champion": "",
                "phases": {}
            }
        players[email]["score"] += pts
        players[email]["phases"][phase] = {
            "score": pts, "details": det,
            "submittedAt": bet.get("submittedAt", "")
        }
        if phase == "final" and bet.get("champion"):
            players[email]["champion"] = bet["champion"]

    return list(players.values())


# ─────────────────────── routes ─────────────────────────
@app.route("/api/results", methods=["GET"])
def get_results():
    with _lock:
        return jsonify(_read(RES_FILE, {}))


@app.route("/api/results", methods=["POST"])
def save_results():
    if request.headers.get("X-Admin-Pin", "") != ADMIN_PIN:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    with _lock:
        ex = _read(RES_FILE, {})
        # Merge group stage scores
        if "groupStage" in data:
            ex.setdefault("groupStage", {}).update(data.pop("groupStage"))
        # Merge phase state flags
        if "phaseState" in data:
            ex.setdefault("phaseState", {}).update(data.pop("phaseState"))
        ex.update(data)
        ex["updatedAt"] = datetime.now(timezone.utc).isoformat()
        _write(RES_FILE, ex)
    return jsonify({"ok": True})


@app.route("/api/bet", methods=["POST"])
def save_bet():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    pl    = data.get("player") or {}
    name  = (pl.get("name")  or "").strip()
    email = (pl.get("email") or "").lower().strip()
    phone = (pl.get("phone") or "").strip()
    if not name or not email:
        return jsonify({"error": "Missing name or email"}), 400
    phase = data.get("phase")
    if phase not in PHASES:
        return jsonify({"error": f"Invalid phase: {phase}"}), 400

    with _lock:
        results     = _read(RES_FILE, {})
        phase_state = results.get("phaseState") or {}
        if not phase_state.get(f"{phase}Open", False):
            return jsonify({"error": f"Fase '{phase}' não está aberta para palpites."}), 403

        bets = _read(BETS_FILE, [])
        # Replace existing bet for same player+phase
        bets = [
            b for b in bets
            if not (
                (b.get("player") or {}).get("email", "").lower().strip() == email
                and b.get("phase") == phase
            )
        ]
        bid = f"bet_{phase}_{int(datetime.now(timezone.utc).timestamp()*1000)}"
        record = {
            "id":    bid,
            "phase": phase,
            **{k: v for k, v in data.items() if k not in ("id",)},
            "player": {"name": name, "email": email, "phone": phone},
            "submittedAt": datetime.now(timezone.utc).isoformat(),
        }
        bets.append(record)
        _write(BETS_FILE, bets)

    app.logger.info(f"Bet saved: {bid} – {name} – phase={phase}")
    return jsonify({"ok": True, "id": bid}), 201


@app.route("/api/mybet", methods=["GET"])
def my_bet():
    email = (request.args.get("email") or "").lower().strip()
    phone = (request.args.get("phone") or "").strip()
    if not email:
        return jsonify({"error": "Email obrigatório / Email required"}), 400
    if not phone:
        return jsonify({"error": "Telefone obrigatório / Phone required"}), 400
    phone_norm = _norm_phone(phone)
    with _lock:
        bets    = _read(BETS_FILE, [])
        results = _read(RES_FILE, {})
    mine = [
        b for b in bets
        if (b.get("player") or {}).get("email", "").lower().strip() == email
        and _norm_phone((b.get("player") or {}).get("phone", "")) == phone_norm
    ]
    if not mine:
        return jsonify({"error":
            "Palpite não encontrado. Verifique email e telefone. / "
            "Bet not found. Check your email and phone."}), 404
    out = []
    for b in mine:
        pts, det = score_bet(b, results)
        out.append({
            "id":          b["id"],
            "phase":       b.get("phase", "groups"),
            "player":      {"name": (b.get("player") or {}).get("name", ""),
                            "email": email},
            "groupScores": b.get("groupScores"),
            "picks":       b.get("picks"),
            "champion":    b.get("champion"),
            "third":       b.get("third"),
            "score":       pts,
            "details":     det,
            "submittedAt": b.get("submittedAt", ""),
        })
    # Sort by phase order, then date
    order = {p: i for i, p in enumerate(PHASES)}
    out.sort(key=lambda x: (order.get(x["phase"], 99), x["submittedAt"]))
    return jsonify({"email": email, "count": len(out), "bets": out})


@app.route("/api/scoreboard", methods=["GET"])
def scoreboard():
    with _lock:
        bets    = _read(BETS_FILE, [])
        results = _read(RES_FILE, {})
        payees  = _read(PAYEES_FILE, {})
    rows = _aggregate_players(bets, results)
    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["paid"] = payees.get(r["email"], False)
    return jsonify({"count": len(rows), "scoreboard": rows})


@app.route("/api/payees", methods=["GET"])
def get_payees():
    if request.headers.get("X-Admin-Pin", "") != ADMIN_PIN:
        return jsonify({"error": "Unauthorized"}), 403
    with _lock:
        payees = _read(PAYEES_FILE, {})
    return jsonify({"payees": payees})


@app.route("/api/payees", methods=["POST"])
def set_payee():
    if request.headers.get("X-Admin-Pin", "") != ADMIN_PIN:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    email = (data.get("email") or "").lower().strip()
    paid  = bool(data.get("paid", False))
    if not email:
        return jsonify({"error": "Email required"}), 400
    with _lock:
        payees = _read(PAYEES_FILE, {})
        if paid:
            payees[email] = True
        else:
            payees.pop(email, None)
        _write(PAYEES_FILE, payees)
    return jsonify({"ok": True, "email": email, "paid": paid})


@app.route("/api/score", methods=["GET"])
def player_score():
    email = (request.args.get("email") or "").lower().strip()
    if not email:
        return jsonify({"error": "email required"}), 400
    with _lock:
        bets    = _read(BETS_FILE, [])
        results = _read(RES_FILE, {})
    mine = [b for b in bets
            if (b.get("player") or {}).get("email", "").lower().strip() == email]
    if not mine:
        return jsonify({"error": "No bets found for this email"}), 404
    out   = []
    total = 0
    for b in mine:
        pts, det = score_bet(b, results)
        total += pts
        out.append({
            "id":          b["id"],
            "phase":       b.get("phase", ""),
            "name":        (b.get("player") or {}).get("name", ""),
            "score":       pts,
            "details":     det,
            "submittedAt": b.get("submittedAt", ""),
        })
    order = {p: i for i, p in enumerate(PHASES)}
    out.sort(key=lambda x: order.get(x["phase"], 99))
    return jsonify({"email": email, "total": total, "bets": out})


@app.route("/api/health", methods=["GET"])
def health():
    with _lock:
        nb  = len(_read(BETS_FILE, []))
        res = _read(RES_FILE, {})
    return jsonify({
        "status":      "ok",
        "bets":        nb,
        "phase_state": res.get("phaseState", {}),
        "champion":    res.get("champion", ""),
    })


if __name__ == "__main__":
    host = os.environ.get("GSY_HOST", "127.0.0.1")
    port = int(os.environ.get("GSY_PORT", "5051"))
    print(f"[GSYFIFA2026] API on http://{host}:{port}  admin_pin={ADMIN_PIN}")
    app.run(host=host, port=port, debug=False)
