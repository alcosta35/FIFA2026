#!/usr/bin/env python3
"""
WC2026 Bolão – API Server v2
Endpoints: /api/bet  /api/mybet  /api/results  /api/scoreboard  /api/score  /api/health

Env vars:
  WC_DATA_DIR   (default /opt/wc2026)
  WC_HOST       (default 127.0.0.1)
  WC_PORT       (default 5050)
  WC_ADMIN_PIN  (default wc2026)  ← change in service file!
"""
import json, os, threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

app  = Flask(__name__)
CORS(app)

DATA_DIR  = os.environ.get("WC_DATA_DIR",  "/opt/wc2026")
BETS_FILE = os.path.join(DATA_DIR, "bets.json")
RES_FILE  = os.path.join(DATA_DIR, "results.json")
ADMIN_PIN = os.environ.get("WC_ADMIN_PIN", "wc2026")
GK        = list("ABCDEFGHIJKL")
_lock     = threading.Lock()


# ── helpers ──────────────────────────────────────────────────
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


# ── scoring ──────────────────────────────────────────────────
def score_bet(bet, results):
    pts, det = 0, {"groups":0,"r32":0,"r16":0,"qf":0,"sf":0,"final":0}

    # Group stage: key = "A_0" .. "L_5"
    gs = results.get("groupStage", {})
    for g in GK:
        grp = bet.get("groupScores", {}).get(g) or []
        for i in range(6):
            res  = gs.get(f"{g}_{i}")
            pred = grp[i] if i < len(grp) else {}
            if not res or not pred: continue
            rh, ra = res.get("h"), res.get("a")
            ph, pa = pred.get("h"), pred.get("a")
            if any(x is None or x=="" for x in [rh,ra,ph,pa]): continue
            rh,ra,ph,pa = int(rh),int(ra),int(ph),int(pa)
            if rh==ph and ra==pa:              pts+=3; det["groups"]+=3
            elif (rh>ra)==(ph>pa) and (rh==ra)==(ph==pa):
                                               pts+=1; det["groups"]+=1

    # KO rounds
    for bk,rk,p in [("r32picks","r32",2),("r16picks","r16",3),
                    ("qfpicks","qf",4),("sfpicks","sf",5)]:
        for idx,team in results.get(rk,{}).items():
            if (bet.get(bk) or {}).get(str(idx))==team:
                pts+=p; det[rk]+=p

    # Final placements
    for field,p in [("champion",10),("runnerUp",7),("third",5),("fourth",3)]:
        if results.get(field) and bet.get(field)==results[field]:
            pts+=p; det["final"]+=p

    return pts, det


# ── routes ────────────────────────────────────────────────────
@app.route("/api/bet", methods=["POST"])
def save_bet():
    data = request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON body"}), 400
    pl = data.get("player", {})
    if not pl.get("name") or not pl.get("email"):
        return jsonify({"error":"Missing name or email"}), 400
    with _lock:
        bets = _read(BETS_FILE, [])
        bid  = f"bet_{int(datetime.now(timezone.utc).timestamp()*1000)}"
        bets.append({"id":bid,**data,"submittedAt":datetime.now(timezone.utc).isoformat()})
        _write(BETS_FILE, bets)
    app.logger.info(f"Saved bet {bid} – {pl.get('name')}")
    return jsonify({"ok":True,"id":bid}), 201


@app.route("/api/bets", methods=["GET"])
def list_bets():
    with _lock: bets = _read(BETS_FILE, [])
    if request.args.get("full")=="1":
        return jsonify({"count":len(bets),"bets":bets})
    return jsonify({"count":len(bets),"bets":[
        {"id":b["id"],"name":b.get("player",{}).get("name",""),
         "email":b.get("player",{}).get("email",""),
         "champion":b.get("champion",""),
         "submittedAt":b.get("submittedAt","")} for b in bets]})


@app.route("/api/mybet", methods=["GET"])
def my_bet():
    """Lookup own bet by email + phone (read-only, no auth token needed)."""
    email = (request.args.get("email") or "").lower().strip()
    phone = (request.args.get("phone") or "").strip()
    if not email:
        return jsonify({"error": "Email obrigatório / Email required"}), 400
    if not phone:
        return jsonify({"error": "Telefone obrigatório / Phone required"}), 400

    def _norm(p):
        """Keep digits only for comparison."""
        return "".join(c for c in p if c.isdigit())

    phone_norm = _norm(phone)
    with _lock:
        bets = _read(BETS_FILE, [])

    mine = [
        b for b in bets
        if b.get("player", {}).get("email", "").lower() == email
        and _norm(b.get("player", {}).get("phone", "")) == phone_norm
    ]
    if not mine:
        return jsonify({"error":
            "Palpite não encontrado. Verifique email e telefone. / "
            "Bet not found. Check your email and phone."}), 404

    out = []
    for b in mine:
        out.append({
            "id":          b["id"],
            "player":      {"name": b.get("player", {}).get("name", ""),
                            "email": email},
            "groupScores": b.get("groupScores", {}),
            "r32picks":    b.get("r32picks",   {}),
            "r16picks":    b.get("r16picks",   {}),
            "qfpicks":     b.get("qfpicks",    {}),
            "sfpicks":     b.get("sfpicks",    {}),
            "champion":    b.get("champion",   ""),
            "runnerUp":    b.get("runnerUp",   ""),
            "third":       b.get("third",      ""),
            "fourth":      b.get("fourth",     ""),
            "submittedAt": b.get("submittedAt",""),
        })
    # Newest first
    out.sort(key=lambda x: x["submittedAt"], reverse=True)
    return jsonify({"email": email, "count": len(out), "bets": out})


@app.route("/api/results", methods=["GET"])
def get_results():
    with _lock: return jsonify(_read(RES_FILE, {}))


@app.route("/api/results", methods=["POST"])
def save_results():
    if request.headers.get("X-Admin-Pin","") != ADMIN_PIN:
        return jsonify({"error":"Unauthorized"}), 403
    data = request.get_json(silent=True)
    if not data: return jsonify({"error":"No data"}), 400
    with _lock:
        ex = _read(RES_FILE, {})
        if "groupStage" in data:
            ex.setdefault("groupStage",{}).update(data.pop("groupStage"))
        ex.update(data)
        ex["updatedAt"] = datetime.now(timezone.utc).isoformat()
        _write(RES_FILE, ex)
    return jsonify({"ok":True})


@app.route("/api/scoreboard", methods=["GET"])
def scoreboard():
    with _lock:
        bets    = _read(BETS_FILE, [])
        results = _read(RES_FILE,  {})
    rows = []
    for b in bets:
        s,d = score_bet(b, results)
        rows.append({"id":b["id"],
            "name":b.get("player",{}).get("name",""),
            "email":b.get("player",{}).get("email",""),
            "champion":b.get("champion",""),
            "score":s,"details":d,"submittedAt":b.get("submittedAt","")})
    rows.sort(key=lambda x:x["score"], reverse=True)
    for i,r in enumerate(rows): r["rank"]=i+1
    return jsonify({"count":len(rows),"scoreboard":rows})


@app.route("/api/score", methods=["GET"])
def player_score():
    email = (request.args.get("email") or "").lower().strip()
    if not email: return jsonify({"error":"email required"}), 400
    with _lock:
        bets    = _read(BETS_FILE, [])
        results = _read(RES_FILE,  {})
    mine = [b for b in bets if b.get("player",{}).get("email","").lower()==email]
    if not mine: return jsonify({"error":"No bets found for this email"}), 404
    out = []
    for b in mine:
        s,d = score_bet(b, results)
        out.append({"id":b["id"],"name":b.get("player",{}).get("name",""),
            "champion":b.get("champion",""),"score":s,"details":d,
            "submittedAt":b.get("submittedAt","")})
    out.sort(key=lambda x:x["score"], reverse=True)
    return jsonify({"email":email,"bets":out})


@app.route("/api/health", methods=["GET"])
def health():
    with _lock:
        nb  = len(_read(BETS_FILE, []))
        res = _read(RES_FILE, {})
    return jsonify({"status":"ok","bets":nb,
        "results_groups":len(res.get("groupStage",{})),
        "champion":res.get("champion","")})


if __name__ == "__main__":
    host = os.environ.get("WC_HOST","127.0.0.1")
    port = int(os.environ.get("WC_PORT","5050"))
    print(f"[WC2026] API on http://{host}:{port}  admin_pin={ADMIN_PIN}")
    app.run(host=host, port=port, debug=False)
