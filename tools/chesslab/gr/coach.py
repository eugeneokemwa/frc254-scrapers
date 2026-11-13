"""Turn an analysed game into coaching text: move notes, verdict, tips, study plan, puzzle prompts."""
import json
import re
import shutil
import subprocess

import chess

from . import phase

BAD = ("inaccuracy", "mistake", "miss", "blunder")
TIP_LIBRARY = {
    "allows_pin": ("Check every line out of your king before you move",
                   "Look along the diagonals and files from your king. If your queen, or a piece that guards something, "
                   "stands on that line, ask whether a bishop or rook can pin it there.", "pin"),
    "allows_fork": ("Watch the squares where enemy knights and pawns can fork",
                    "Before moving, check which of your valuable pieces share a knight jump or a pawn diagonal. "
                    "Two loose pieces attacked at once usually costs one of them.", "fork"),
    "loses_material": ("Blunder-check: what does my move leave undefended?",
                       "After choosing a move, look at every capture and check your opponent can make next. "
                       "Most lost material in club games is a piece left hanging, not a deep combination.", "hangingPiece"),
    "missed_material": ("Look for captures, checks and threats first",
                        "On each move, list your forcing moves (checks, captures, threats) before the quiet ones. "
                        "Several wins of material were available in this game.", "advantage"),
    "missed_fork": ("Spot double attacks for yourself",
                    "When two enemy pieces are loose or the king is exposed, look for a knight, queen or pawn move "
                    "that hits both at once.", "fork"),
    "allows_mate": ("Keep the king covered when pieces come close",
                    "When enemy pieces gather near your king, count attackers against defenders and check for "
                    "back-rank and mating patterns before every move.", "mateIn2"),
    "missed_mate": ("Always check forcing lines when the enemy king is exposed",
                    "A forced mate was on the board. When the king is short of squares, try every check first.", "mateIn3"),
    "king_pawn_push": ("Don't push the pawns in front of your castled king without a reason",
                       "Each push opens lines and squares near your king, and they can never move back. "
                       "If you do push (say f4), tuck the king into the corner (Kh1/Kh8) first.", "exposedKing"),
    "tempo_loss": ("One piece, one move in the opening",
                   "Moving the same minor piece twice before development is finished hands your opponent free moves.", "opening"),
    "early_queen": ("Develop knights and bishops before the queen",
                    "An early queen gets chased around, and every chase gives your opponent a free developing move.", "opening"),
    "passive_king": ("In endgames, march the king to the centre",
                     "With queens off, the king is a fighting piece. Head for the centre and the enemy pawns, not the corner.", "endgame"),
}
THEME_LABEL = {"pin": "Pin", "fork": "Fork", "hangingPiece": "Hanging piece", "advantage": "Advantage",
               "mateIn2": "Mate in 2", "mateIn3": "Mate in 3", "exposedKing": "Exposed king",
               "opening": "Opening", "endgame": "Endgame"}
PHASE_TIP = {
    "setup": ("Learn the opening by system, not by memory",
              "Your opening book was the weakest part. Replay the main lines of your openings and check you know the "
              "typical plans behind them."),
    "opening": ("Learn the plans of your openings, not just the moves",
                "Your early game was the weakest part. Pick one main line for each colour and study 3 model games in it."),
    "middlegame": ("Slow down at the critical moments",
                   "Your middlegame accuracy was the lowest stage. When the position opens up or a capture sequence starts, "
                   "spend extra time and check your opponent's forcing replies."),
    "tactics": ("Spot the critical move before it happens",
                "Your middlegame was the weakest part. Before each capture, ask what the opponent can take back, and "
                "practise forcing sequences in the position you actually met."),
    "endgame": ("Study basic endgame technique",
                "Your endgame accuracy was the lowest stage. King activity, rook behind passed pawns and simple pawn "
                "endings win many club games."),
}


def mv_label(m):
    return f"{(m['ply'] + 1) // 2}{'.' if m['white'] else '...'} {m['san']}"


def side_word(white_to_move):
    return "White" if white_to_move else "Black"


def motif_sentence(mt, m):
    t = mt["type"]
    if t == "allows_mate":
        return f"This allows a forced mate in {mt['n']}."
    if t == "missed_mate":
        return f"There was a forced mate in {mt['n']}, starting with {m['bestSan']}."
    if t == "loses_material":
        pts = f"{mt['amount']} point{'s' if mt['amount'] > 1 else ''}"
        if mt.get("piece") and mt["piece"] != "pawn":
            return f"It leaves the {mt['piece']} on {mt['square']} hanging: {mt['reply']} wins it."
        if mt.get("line"):
            return f"It loses material by force: {mt['line']} leaves you about {pts} down."
        return f"It drops material: after {mt['reply']} you come out about {pts} down."
    if t == "missed_material":
        return f"{mt['best']} would have won material (about {mt['amount']} point{'s' if mt['amount'] > 1 else ''})."
    if t == "allows_fork":
        return f"It allows {mt['reply']}, hitting the {' and the '.join(mt['targets'][:2])} at once."
    if t == "allows_pin":
        return f"After {mt['reply']}, the {mt['piece']} on {mt['square']} is pinned to the king."
    if t == "missed_fork":
        return f"{mt['best']} was a double attack on the {' and the '.join(mt['targets'][:2])}."
    if t == "king_pawn_push":
        return f"Pushing the {mt['file']}-pawn loosens the shield in front of the king."
    if t == "early_queen":
        return "Bringing the queen out this early invites attacks on it with tempo."
    if t == "tempo_loss":
        return f"The {mt['piece']} moves for a second time in the opening, losing a tempo."
    if t == "passive_king":
        return f"The king should head for the centre in this endgame; {mt['best']} was more active."
    return ""


def eval_state(wp):
    if wp >= 90:
        return "winning"
    if wp >= 65:
        return "clearly better"
    if wp >= 55:
        return "slightly better"
    if wp > 45:
        return "equal"
    if wp > 35:
        return "slightly worse"
    if wp > 10:
        return "clearly worse"
    return "lost"


def move_note(m, prev_wp_white, names):
    who = names["White"] if m["white"] else names["Black"]
    before = prev_wp_white if m["white"] else 100 - prev_wp_white
    after = m["wp"] if m["white"] else 100 - m["wp"]
    parts = [motif_sentence(mt, m) for mt in m.get("motifs", [])]
    parts = [p for p in parts if p]
    cls = m["cls"]
    if cls == "blunder":
        lead = f"A turning point: {who} goes from {eval_state(before)} to {eval_state(after)}."
    elif cls == "mistake":
        lead = f"A real mistake: the position goes from {eval_state(before)} to {eval_state(after)} for {who}."
    elif cls == "miss":
        lead = "The opponent had just erred, and this lets the chance slip."
    elif cls == "inaccuracy":
        lead = "Not the most precise."
    elif cls == "great":
        lead = "The only good move here: anything else gives a lot away."
    elif cls == "brilliant":
        lead = "A strong sacrifice that the engine fully approves."
    else:
        lead = ""
    if cls in BAD and m["bestSan"]:
        parts.append(f"Better was {m['bestSan']}.")
    if not lead and not parts:
        return None
    return " ".join([lead] + parts).strip()


def coach(review, me_white, names):
    moves = review["moves"]
    me = "white" if me_white else "black"
    opp_name = names["Black"] if me_white else names["White"]
    my_name = names["White"] if me_white else names["Black"]

    # per-move notes
    prev = review["startWp"]
    for m in moves:
        n = move_note(m, prev, names)
        if n:
            m["note"] = n
        prev = m["wp"]

    def my_wp(wp_white):
        return wp_white if me_white else 100 - wp_white

    mine = [m for m in moves if m["white"] == me_white]
    theirs = [m for m in moves if m["white"] != me_white]
    result = review["headers"].get("Result", "*")
    outcome = {"1-0": "win" if me_white else "loss", "0-1": "loss" if me_white else "win", "1/2-1/2": "draw"}.get(result, "unfinished")
    s = review["summary"]
    my_acc, opp_acc = s[me]["acc"], s["black" if me_white else "white"]["acc"]

    # turning point: largest win% drop for the side that didn't win
    pool = mine if outcome in ("loss", "draw", "unfinished") else theirs
    tp = max(pool, key=lambda m: m["loss"]) if pool else None
    tp_before = None
    if tp:
        prev_wp = moves[tp["ply"] - 2]["wp"] if tp["ply"] >= 2 else review["startWp"]
        tp_before = my_wp(prev_wp)

    blunders = [m for m in mine if m["cls"] == "blunder"]
    mistakes = [m for m in mine if m["cls"] in ("mistake", "miss")]
    peak = max(mine, key=lambda m: my_wp(m["wp"]), default=None)

    if outcome == "win":
        verdict = f"You beat {opp_name} with {my_acc}% accuracy (vs {opp_acc}%)."
        if tp and tp["loss"] >= 10:
            verdict += f" The game turned on {mv_label(tp)}, {opp_name}'s {tp['cls']}."
    elif outcome == "loss":
        verdict = f"You lost to {opp_name} with {my_acc}% accuracy (vs {opp_acc}%)."
        if tp and tp_before is not None:
            verdict += f" You were {eval_state(tp_before)} until {mv_label(tp)}"
            verdict += f", which was a {tp['cls']}." if tp["cls"] in BAD else "."
    elif outcome == "draw":
        verdict = f"A draw with {opp_name}: {my_acc}% accuracy against {opp_acc}%."
    else:
        verdict = f"Accuracy: you {my_acc}%, {opp_name} {opp_acc}%."
    if peak and my_wp(peak["wp"]) >= 75 and outcome != "win":
        verdict += f" You had a {eval_state(my_wp(peak['wp']))} position after {mv_label(peak)} ({peak['eval']})."

    # which part of the game was this?
    ph_info = review.get("phases") or {}
    phase_line = ph_info.get("text") or ""
    if phase_line:
        verdict += f" Stages: {phase_line}."
        critical = next((x for x in ph_info.get("phases", []) if x["phase"] == "tactics"), None)
        if critical:
            verdict += (f" The sharpest stretch ran from move {(critical['from'] + 1) // 2} to move {(critical['to'] + 1) // 2}.")
        elif ph_info.get("names", [])[-1:] == ["endgame"]:
            verdict += " The whole game was decided in the endgame."

    turning = ""
    if tp and tp["loss"] >= 8:
        sentences = [motif_sentence(mt, tp) for mt in tp.get("motifs", [])]
        sentences = [x for x in sentences if x]
        turning = f"{mv_label(tp)} ({tp['cls']}, {tp['eval']}). " + (" ".join(sentences[:2]) + " " if sentences else "")
        if tp["bestSan"] and tp["cls"] in BAD:
            turning += f"Best was {tp['bestSan']} ({tp['bestEval']})."

    # strengths
    good = []
    ph = s[me]["phases"]
    for name in ("setup", "opening", "tactics", "endgame", "middlegame"):
        v = ph.get(name)
        if v is not None and v >= 90:
            good.append(f"Strong {phase.LABEL.get(name, name)}: {v:.0f}% accuracy.")
    for m in mine:
        if m["cls"] in ("brilliant", "great"):
            good.append(f"{mv_label(m)} was {'a brilliant' if m['cls'] == 'brilliant' else 'a great'} move, the only one that kept the position.")
    if peak and my_wp(peak["wp"]) >= 65:
        good.append(f"You got a {eval_state(my_wp(peak['wp']))} position by {mv_label(peak)} ({peak['eval']}).")
    if not blunders:
        good.append("No blunders the whole game.")
    if review["summary"]["counts"]["book"][0 if me_white else 1] >= 4:
        good.append(f"You knew the opening: {review['opening']}.")
    if not good:
        good.append(f"You kept fighting for {len(mine)} moves; use the key moments below to see where it slipped.")

    # weaknesses
    bad = []
    for m in sorted([m for m in mine if m["cls"] in BAD], key=lambda m: -m["loss"])[:5]:
        sentences = [motif_sentence(mt, m) for mt in m.get("motifs", [])]
        sentences = [x for x in sentences if x]
        why = sentences[0] if sentences else f"The evaluation went from {m['bestEval']} (after {m['bestSan']}) to {m['eval']}."
        bad.append(f"{mv_label(m)} ({m['cls']}): {why}")
    motif_count = {}
    for m in mine:
        for mt in m.get("motifs", []):
            weight = 1 + (m["loss"] / 10)
            motif_count[mt["type"]] = motif_count.get(mt["type"], 0) + weight
    if motif_count.get("tempo_loss"):
        bad.append("Lost time in the opening by moving the same piece twice.")
    if motif_count.get("king_pawn_push", 0) >= 1 and any(mt["type"] == "king_pawn_push" for m in mine if m["cls"] in BAD for mt in m["motifs"]):
        bad.append("Pawn pushes in front of your own king created weaknesses that were exploited.")
    if not bad:
        bad.append("No serious errors. Compare your moves with the engine's best moves to find small improvements.")

    # tips
    tips, themes = [], []
    for t, _ in sorted(motif_count.items(), key=lambda x: -x[1]):
        if t in TIP_LIBRARY:
            title, body, theme = TIP_LIBRARY[t]
            tips.append({"title": title, "body": body})
            if theme not in themes:
                themes.append(theme)
        if len(tips) >= 4:
            break
    phases = {k: v for k, v in ph.items() if v is not None}
    if phases:
        worst = min(phases, key=phases.get)
        if phases[worst] < 92:
            title, body = PHASE_TIP.get(worst, PHASE_TIP["middlegame"])
            tips.append({"title": title, "body": body})
    if len(tips) < 3:
        tips.append({"title": "Use a thinking checklist", "body": "Every move: what did my opponent's move threaten? What are my checks, "
                     "captures and threats? Which of my pieces are undefended after my move?"})

    plan = []
    for th in themes[:3]:
        plan.append({"text": f"Solve 15 puzzles on the “{THEME_LABEL.get(th, th)}” theme.",
                     "url": f"https://lichess.org/training/{th}"})
    plan.append({"text": "Redo the practice positions on this page until you find every answer without the hint.", "url": ""})
    plan.append({"text": f"Replay 3 games from “Similar games” without the engine, noting the plans in the {review['opening'].split(':')[0]}.", "url": ""})

    # puzzle prompts
    for p in review.get("puzzles", []):
        m = moves[p["ply"] - 1]
        side = "White" if p["color"] == "white" else "Black"
        mvno = f"{(p['ply'] + 1) // 2}{'' if p['color'] == 'white' else '...'}"
        b = chess.Board(p["fen"])
        bm = chess.Move.from_uci(p["good"][0]) if p["good"] else None
        if p["kind"] == "yours":
            p["title"] = f"Move {mvno}: find a better move"
            p["prompt"] = f"{side} to play. In the game you played {p['played']} ({p['playedCls']}). Find something stronger."
        else:
            p["title"] = f"Move {mvno}: find {opp_name}'s best move"
            p["prompt"] = f"{side} to play. {opp_name} found a strong move here. Can you?"
        if len(p["good"]) > 1:
            p["prompt"] += f" {len(p['good'])} moves are good enough."
        hint = "Look at every check, capture and threat first."
        if bm:
            piece = b.piece_at(bm.from_square)
            pname = chess.piece_name(piece.piece_type) if piece else "piece"
            if b.gives_check(bm):
                hint = f"It's a check with the {pname}."
            elif b.is_capture(bm):
                hint = f"Your {pname} can capture something."
            elif any(mt["type"] == "allows_pin" for mt in m.get("motifs", [])):
                hint = "Watch out for a pin: get the piece or the king off the line."
            elif piece and piece.piece_type == chess.KING:
                hint = "Think about the king's safety or activity."
            else:
                hint = f"Consider a {pname} move."
        p["hint"] = hint

    return {"verdict": verdict, "turning": turning, "good": good[:5], "bad": bad[:6], "tips": tips[:5],
            "plan": plan, "source": "auto"}


# ---------------------------------------------------------------------------
def claude_available():
    return shutil.which("claude") is not None


def ai_rewrite(review, me_white, names, timeout=300):
    """Ask the local Claude Code CLI to rewrite the coaching text. Returns (insights, notes) or raises."""
    moves = review["moves"]
    key = [m for m in moves if m["cls"] in BAD + ("great", "brilliant")]
    compact = {
        "players": names, "you": "White" if me_white else "Black", "result": review["headers"].get("Result"),
        "event": review["headers"].get("Event"), "opening": f"{review['eco']} {review['opening']}",
        "accuracy": {k: review["summary"][k]["acc"] for k in ("white", "black")},
        "phaseAccuracy": {k: review["summary"][k]["phases"] for k in ("white", "black")},
        "moves": " ".join(m["san"] for m in moves),
        "keyMoves": [{"ply": m["ply"], "move": mv_label(m), "class": m["cls"], "evalAfter": m["eval"],
                      "best": m["bestSan"], "bestLine": m["bestLine"], "bestEval": m["bestEval"],
                      "engineReply": m["reply"], "motifs": m.get("motifs", [])} for m in key][:25],
        "draft": review["insights"],
    }
    prompt = (
        "You are a friendly, precise chess coach writing a game review for a club player. "
        "Use ONLY the engine facts given; never invent variations or evaluations. Plain English, short sentences, "
        "address the player as 'you'. Return ONLY a JSON object with keys: "
        "verdict (2-3 sentences), turning (1-3 sentences), good (list of 2-5 strings), bad (list of 2-6 strings), "
        "tips (list of 3-5 objects {title, body}), notes (object mapping ply number as string to a 1-3 sentence comment, "
        "for each key move). Data:\n" + json.dumps(compact, ensure_ascii=False)
    )
    proc = subprocess.run(["claude", "-p", "--output-format", "text"], input=prompt, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "claude failed").strip()[:400])
    text = proc.stdout
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise RuntimeError("Claude did not return JSON")
    data = json.loads(match.group(0))
    insights = dict(review["insights"])
    for k in ("verdict", "turning", "good", "bad", "tips"):
        if data.get(k):
            insights[k] = data[k]
    insights["source"] = "claude"
    notes = {int(k): v for k, v in (data.get("notes") or {}).items() if str(k).isdigit()}
    return insights, notes
