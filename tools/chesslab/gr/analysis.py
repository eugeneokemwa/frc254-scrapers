"""Stockfish game analysis: evals, move classification, accuracy, motifs, puzzles."""
import math
import shutil
import statistics
import threading

import chess
import chess.engine

from . import openings, phase

VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
PIECE_NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
              chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
DEPTHS = {"quick": (14, 2.0), "standard": (18, 4.0), "deep": (22, 8.0)}


def engine_path():
    return shutil.which("stockfish")


def win_pct(cp, mate):
    if mate is not None:
        return 100.0 if mate > 0 else 0.0
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def line_wp(line):
    return win_pct(line["cp"], line["mate"])


def eval_label(line):
    if line["mate"] is not None:
        return ("#" if line["mate"] > 0 else "#-") + str(abs(line["mate"]))
    return f"{line['cp'] / 100:+.1f}"


def material(board, color):
    return sum(VAL[p.piece_type] for p in board.piece_map().values() if p.color == color)


def balance(board, color):
    return material(board, color) - material(board, not color)


def pack_lines(board, infos):
    out = []
    for info in infos:
        score = info["score"].white()
        pv = info.get("pv", [])[:10]
        out.append({"cp": score.score(), "mate": score.mate(), "pv": [m.uci() for m in pv],
                    "pvsan": board.variation_san(pv) if pv else ""})
    return out


def line_balance(board, ucis, color, plies):
    """Material balance for `color` after playing up to `plies` moves of a line."""
    b = board.copy()
    for u in ucis[:plies]:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            break
        b.push(mv)
    return balance(b, color)


def is_sacrifice(board, move):
    piece = board.piece_at(move.from_square)
    captured = board.piece_at(move.to_square)
    if piece is None or VAL[piece.piece_type] < 3:
        return False
    b = board.copy()
    b.push(move)
    gain = VAL[captured.piece_type] if captured else 0
    attackers = b.attackers(b.turn, move.to_square)
    if not attackers:
        return False
    cheapest = min(VAL[b.piece_at(s).piece_type] or 99 for s in attackers)
    defended = bool(b.attackers(not b.turn, move.to_square))
    return (not defended and VAL[piece.piece_type] > gain) or (cheapest + gain < VAL[piece.piece_type])


def new_pins(before, after, color):
    """Pieces of `color` (value >= 3) pinned in `after` but not in `before`."""
    found = []
    for sq, p in after.piece_map().items():
        if p.color != color or VAL[p.piece_type] < 3 or p.piece_type == chess.KING:
            continue
        if after.is_pinned(color, sq):
            prev = before.piece_at(sq)
            if not (prev == p and before.is_pinned(color, sq)):
                found.append((sq, p))
    return found


def fork_targets(board, move):
    """Valuable enemy pieces attacked by the piece that just moved."""
    b = board.copy()
    b.push(move)
    mover = b.piece_at(move.to_square)
    if mover is None:
        return []
    targets = []
    for sq in b.attacks(move.to_square):
        p = b.piece_at(sq)
        if p and p.color != mover.color and (p.piece_type == chess.KING or VAL[p.piece_type] > VAL[mover.piece_type]
                                              or (VAL[p.piece_type] >= 3 and not b.attackers(p.color, sq))):
            targets.append((sq, p))
    return targets if len(targets) >= 2 else []


def detect_motifs(board, move, prev_line, cur_line, ply, history, loss=0.0):
    """Return a list of motif dicts describing what this move did or allowed."""
    color = board.turn
    motifs = []
    after = board.copy()
    after.push(move)
    best_pv = prev_line["pv"] if prev_line else []
    reply = cur_line["pv"] if cur_line else []

    # mates
    if cur_line and cur_line["mate"] is not None and (cur_line["mate"] < 0) == (color == chess.WHITE):
        if not (prev_line and prev_line["mate"] is not None and (prev_line["mate"] < 0) == (color == chess.WHITE)):
            motifs.append({"type": "allows_mate", "n": abs(cur_line["mate"])})
    if prev_line and prev_line["mate"] is not None and (prev_line["mate"] > 0) == (color == chess.WHITE):
        if not (cur_line and cur_line["mate"] is not None and (cur_line["mate"] > 0) == (color == chess.WHITE)):
            motifs.append({"type": "missed_mate", "n": abs(prev_line["mate"])})

    # material: what the opponent's best reply wins, and what our best line would have won
    base = balance(board, color)
    after_reply = line_balance(after, reply, color, 10) if reply else balance(after, color)
    best_gain = line_balance(board, best_pv, color, 10) - base if best_pv else 0
    played_gain = after_reply - base
    if played_gain <= -2 and reply and loss >= 5:
        first = chess.Move.from_uci(reply[0])
        victim = after.piece_at(first.to_square) if after.is_capture(first) else None
        recaptured = False
        if victim and len(reply) > 1:
            b2 = after.copy()
            b2.push(first)
            second = chess.Move.from_uci(reply[1])
            recaptured = second in b2.legal_moves and second.to_square == first.to_square
        clean = victim is not None and not recaptured and VAL[victim.piece_type] >= -played_gain - 1
        motifs.append({"type": "loses_material", "amount": -played_gain,
                       "piece": PIECE_NAME[victim.piece_type] if clean else None,
                       "square": chess.square_name(first.to_square) if clean else None,
                       "reply": after.san(first), "line": after.variation_san([chess.Move.from_uci(u) for u in reply[:6]])})
    if best_gain >= 2 and best_gain - played_gain >= 2 and best_pv and loss >= 5:
        motifs.append({"type": "missed_material", "amount": best_gain,
                       "best": board.san(chess.Move.from_uci(best_pv[0]))})

    # tactics the opponent's reply line creates (only worth mentioning when the move lost ground)
    if reply and loss >= 5:
        r1 = chess.Move.from_uci(reply[0])
        if r1 in after.legal_moves:
            forks = fork_targets(after, r1)
            if forks and not after.is_capture(r1):
                motifs.append({"type": "allows_fork", "reply": after.san(r1),
                               "targets": [PIECE_NAME[p.piece_type] for _, p in forks]})
        b2 = after.copy()
        san_line = []
        for k, u in enumerate(reply[:5]):
            mv2 = chess.Move.from_uci(u)
            if mv2 not in b2.legal_moves:
                break
            san_line.append(b2.san(mv2))
            prev_b = b2.copy()
            b2.push(mv2)
            if b2.turn == color:          # opponent just moved
                pins = new_pins(after, b2, color)
                if pins:
                    sq, p = pins[0]
                    motifs.append({"type": "allows_pin", "reply": " ".join(san_line), "piece": PIECE_NAME[p.piece_type],
                                   "square": chess.square_name(sq)})
                    break
    if best_pv and loss >= 5:
        b1 = chess.Move.from_uci(best_pv[0])
        if b1 in board.legal_moves and b1 != move:
            forks = fork_targets(board, b1)
            if forks:
                motifs.append({"type": "missed_fork", "best": board.san(b1),
                               "targets": [PIECE_NAME[p.piece_type] for _, p in forks]})

    # king safety: pawn pushes in front of our own castled king
    piece = board.piece_at(move.from_square)
    king = board.king(color)
    if piece and piece.piece_type == chess.PAWN and king is not None:
        kf, mf = chess.square_file(king), chess.square_file(move.from_square)
        home_rank = 0 if color == chess.WHITE else 7
        if loss >= 3 and chess.square_rank(king) == home_rank and ((kf >= 5 and mf >= 5) or (kf <= 2 and mf <= 2)):
            motifs.append({"type": "king_pawn_push", "file": "abcdefgh"[mf]})

    # opening discipline
    if ply <= 24 and piece:
        if piece.piece_type == chess.QUEEN and ply <= 10:
            motifs.append({"type": "early_queen"})
        if piece.piece_type in (chess.KNIGHT, chess.BISHOP) and not board.is_capture(move):
            for h in history:
                if h["color"] == color and h["to"] == move.from_square and not h["capture"] and not board.is_check():
                    motifs.append({"type": "tempo_loss", "piece": PIECE_NAME[piece.piece_type]})
                    break

    # endgame king activity
    if piece and piece.piece_type == chess.KING and not board.has_castling_rights(color):
        heavy = sum(VAL[p.piece_type] for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
        if heavy <= 26 and best_pv:
            def centre_dist(sq):
                return max(abs(3.5 - chess.square_file(sq)), abs(3.5 - chess.square_rank(sq)))
            b1 = chess.Move.from_uci(best_pv[0])
            if centre_dist(move.to_square) > centre_dist(move.from_square):
                bp = board.piece_at(b1.from_square)
                if bp and bp.piece_type == chess.KING and centre_dist(b1.to_square) < centre_dist(move.to_square):
                    motifs.append({"type": "passive_king", "best": board.san(b1)})
    return motifs


def classify(i, loss, before, prev, cur, white, moves_so_far, book_until, board, move):
    best = prev["lines"][0]
    second = prev["lines"][1] if len(prev["lines"]) > 1 else None
    is_best = bool(best["pv"]) and best["pv"][0] == cur["uci"]
    if i <= book_until:
        return "book"
    if is_best or loss < 0.5:
        cls = "best"
        recapture = bool(moves_so_far) and cur["uci"][2:4] == moves_so_far[-1]["uci"][2:4]
        if second and not recapture:
            s = line_wp(second)
            s = s if white else 100 - s
            if before - s >= 15 and 10 < before < 97:
                cls = "great"
        if is_sacrifice(board, move) and before < 97 and before - loss > 45:
            cls = "brilliant"
        return cls
    if loss < 2:
        cls = "excellent"
    elif loss < 5:
        cls = "good"
    elif loss < 10:
        cls = "inaccuracy"
    elif loss < 20:
        cls = "mistake"
    else:
        cls = "blunder"
    if moves_so_far and moves_so_far[-1]["cls"] in ("mistake", "blunder") and cls in ("inaccuracy", "mistake") and loss >= 8:
        cls = "miss"
    return cls


def game_accuracy(moves, wps, white):
    n = len(wps)
    win = max(2, min(8, n // 10))
    vols = [max(0.5, min(12, statistics.pstdev(wps[max(0, i - win):i + 1]))) for i in range(1, n)]
    accs, ws = [], []
    for m, v in zip(moves, vols):
        if m["white"] == white:
            accs.append(m["acc"])
            ws.append(v)
    if not accs:
        return None
    wmean = sum(a * w for a, w in zip(accs, ws)) / sum(ws)
    hmean = len(accs) / sum(1 / max(a, 1) for a in accs)
    return round((wmean + hmean) / 2, 1)


def analyze(game, level="standard", progress=None, cancelled=None):
    depth, tcap = DEPTHS.get(level, DEPTHS["standard"])
    limit = chess.engine.Limit(depth=depth, time=tcap)
    mainline = list(game.mainline_moves())
    eco, opening_name, book_until = openings.name_for(mainline)
    eng = chess.engine.SimpleEngine.popen_uci(engine_path())
    try:
        eng.configure({"Threads": 6, "Hash": 256})
        board = game.board()
        start_fen = board.fen()
        plies = [{"fen": board.fen(), "lines": pack_lines(board, eng.analyse(board, limit, multipv=2))}]
        total = len(mainline)
        for idx, mv in enumerate(mainline, 1):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            san = board.san(mv)
            board.push(mv)
            infos = [] if board.is_game_over() else eng.analyse(board, limit, multipv=2)
            plies.append({"san": san, "uci": mv.uci(), "fen": board.fen(), "lines": pack_lines(board, infos)})
            if progress:
                progress(idx / max(total, 1) * 0.85, f"Analysing move {(idx + 1) // 2} of {(total + 1) // 2}")

        # classification
        board = game.board()
        moves, history = [], []
        wps = [line_wp(plies[0]["lines"][0])]
        for i in range(1, len(plies)):
            prev, cur = plies[i - 1], plies[i]
            mv = chess.Move.from_uci(cur["uci"])
            white = board.turn
            best = prev["lines"][0]
            before_w = line_wp(best)
            after_board = board.copy()
            after_board.push(mv)
            if cur["lines"]:
                after_w = line_wp(cur["lines"][0])
            elif after_board.is_checkmate():
                after_w = 0.0 if after_board.turn == chess.WHITE else 100.0
            else:
                after_w = 50.0
            before = before_w if white else 100 - before_w
            after = after_w if white else 100 - after_w
            loss = max(0.0, before - after)
            cls = classify(i, loss, before, prev, cur, white, moves, book_until, board, mv)
            motifs = [] if cls == "book" else detect_motifs(board, mv, best, cur["lines"][0] if cur["lines"] else None, i, history, loss)
            best_san = board.san(chess.Move.from_uci(best["pv"][0])) if best["pv"] else ""
            if cur["lines"]:
                ev = eval_label(cur["lines"][0])
            elif after_board.is_checkmate():
                ev = "1-0" if after_board.turn == chess.BLACK else "0-1"
            else:
                ev = "½-½"
            moves.append({
                "ply": i, "san": cur["san"], "uci": cur["uci"], "fen": cur["fen"], "white": white,
                "cls": cls, "loss": round(loss, 1),
                "acc": round(max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669)), 1),
                "wp": round(after_w, 1), "eval": ev,
                "best": best["pv"][0] if best["pv"] else "", "bestSan": best_san,
                "bestLine": best["pvsan"], "bestEval": eval_label(best),
                "reply": cur["lines"][0]["pvsan"] if cur["lines"] else "",
                "check": after_board.is_check(), "motifs": motifs,
            })
            history.append({"color": white, "to": mv.to_square, "capture": board.is_capture(mv)})
            wps.append(after_w)
            board.push(mv)

        # phases: setup (book) -> opening -> tactics -> endgame
        ph_info = phase.from_moves(moves, setup_end=book_until, start_fen=start_fen, start_wp=wps[0])
        for m in moves:
            m["phase"] = phase.phase_at(ph_info["segments"], m["ply"])

        def phase_acc(white, ph):
            a = [m["acc"] for m in moves if m["white"] == white and m["phase"] == ph]
            return round(sum(a) / len(a), 1) if a else None

        def acpl(white):
            tot = n = 0
            for i, m in enumerate(moves):
                if m["white"] != white:
                    continue
                b, a = plies[i]["lines"][0], (plies[i + 1]["lines"] or [None])[0]
                if b["mate"] is not None or a is None or a["mate"] is not None:
                    continue
                d = (b["cp"] - a["cp"]) if white else (a["cp"] - b["cp"])
                tot += max(0, min(d, 1000))
                n += 1
            return round(tot / max(n, 1))

        classes = ("brilliant", "great", "best", "excellent", "good", "book", "inaccuracy", "mistake", "miss", "blunder")
        phase_names = ("setup", "opening", "tactics", "endgame")
        summary = {
            "white": {"acc": game_accuracy(moves, wps, True), "acpl": acpl(True),
                      "phases": {p: phase_acc(True, p) for p in phase_names}},
            "black": {"acc": game_accuracy(moves, wps, False), "acpl": acpl(False),
                      "phases": {p: phase_acc(False, p) for p in phase_names}},
            "counts": {c: [sum(1 for m in moves if m["white"] and m["cls"] == c),
                           sum(1 for m in moves if not m["white"] and m["cls"] == c)] for c in classes},
            "bookUntil": book_until, "depth": depth, "level": level,
        }
        # keep the old aggregate name alive for older coaching text
        for side in ("white", "black"):
            mid = [m["acc"] for m in moves if m["white"] == (side == "white") and m["phase"] in ("opening", "tactics")]
            summary[side]["phases"]["middlegame"] = round(sum(mid) / len(mid), 1) if mid else None
        if progress:
            progress(0.88, "Building practice positions")
        return {"start": start_fen, "startEval": eval_label(plies[0]["lines"][0]), "startWp": round(wps[0], 1),
                "moves": moves, "summary": summary, "eco": eco, "opening": opening_name,
                "phases": ph_info}, eng
    except Exception:
        eng.quit()
        raise


# ---------------------------------------------------------------------------
# on-demand analysis for the analysis page
# ---------------------------------------------------------------------------
QUICK = {"fast": (10, 0.4), "quick": (14, 1.5)}
NEUTRAL = {"cp": 0, "mate": None, "pv": [], "pvsan": ""}

_ENGINE_LOCK = threading.RLock()
_ENGINE = None


def shared_engine():
    """One Stockfish process reused by the small on-demand position analyses."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = chess.engine.SimpleEngine.popen_uci(engine_path())
            _ENGINE.configure({"Threads": 2, "Hash": 64})
        return _ENGINE


def _position_result(board, line):
    """One position's verdict, from a packed engine line."""
    return {
        "fen": board.fen(),
        "eval": eval_label(line),
        "wp": round(line_wp(line), 1),
        "best": line["pv"][0] if line["pv"] else "",
        "bestSan": board.san(chess.Move.from_uci(line["pv"][0])) if line["pv"] else "",
        "line": line["pvsan"],
    }


def analyse_position(fen, level="quick"):
    """Analyse a single position: eval, best move and the engine's main line."""
    board = chess.Board(fen)
    depth, tcap = QUICK.get(level, QUICK["quick"])
    limit = chess.engine.Limit(depth=depth, time=tcap)
    with _ENGINE_LOCK:
        infos = shared_engine().analyse(board, limit, multipv=2)
    lines = pack_lines(board, infos)
    out = _position_result(board, lines[0] if lines else NEUTRAL)
    out["depth"] = depth
    out["level"] = level
    out["second"] = lines[1]["pvsan"] if len(lines) > 1 else ""
    return out


def analyse_positions(fens, level="fast", progress=None, cancelled=None):
    """The engine verdict for every position of a game (index 0 = the start)."""
    depth, tcap = QUICK.get(level, QUICK["fast"])
    limit = chess.engine.Limit(depth=depth, time=tcap)
    eng = chess.engine.SimpleEngine.popen_uci(engine_path())
    out = []
    try:
        eng.configure({"Threads": 2, "Hash": 64})
        total = len(fens)
        for i, fen in enumerate(fens):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            board = chess.Board(fen)
            if board.is_checkmate():
                out.append({"ply": i, "fen": fen, "eval": "0-1" if board.turn == chess.WHITE else "1-0",
                            "wp": 0.0 if board.turn == chess.WHITE else 100.0,
                            "best": "", "bestSan": "", "line": ""})
            elif board.is_stalemate() or board.is_insufficient_material():
                out.append({"ply": i, "fen": fen, "eval": "½-½", "wp": 50.0,
                            "best": "", "bestSan": "", "line": ""})
            else:
                infos = eng.analyse(board, limit, multipv=1)
                lines = pack_lines(board, infos)
                rec = _position_result(board, lines[0] if lines else NEUTRAL)
                rec["ply"] = i
                out.append(rec)
            if progress:
                progress(i / max(total, 1) * 0.95, f"Analysing position {i + 1} of {total}")
    finally:
        eng.quit()
    return out


def build_puzzles(eng, moves, me_white, limit_n=6):
    """Positions before your worst moves (find the better move) and the opponent's best punishes."""
    picks = []
    mine = sorted([m for m in moves if m["white"] == me_white and m["cls"] in ("blunder", "mistake", "miss", "inaccuracy")],
                  key=lambda m: -m["loss"])
    for m in mine[:6]:
        picks.append((m, "yours"))
    theirs = [m for m in moves if m["white"] != me_white and m["cls"] in ("great", "brilliant")]
    for m in theirs[:2]:
        picks.append((m, "theirs"))
    picks.sort(key=lambda x: x[0]["ply"])
    out = []
    for m, kind in picks[:limit_n]:
        fen = moves[m["ply"] - 2]["fen"] if m["ply"] >= 2 else chess.STARTING_FEN
        b = chess.Board(fen)
        side = b.turn
        n_legal = b.legal_moves.count()
        infos = eng.analyse(b, chess.engine.Limit(depth=14, time=12), multipv=min(n_legal, 40))
        scored = []
        for inf in infos:
            if not inf.get("pv"):
                continue
            mv = inf["pv"][0]
            s = inf["score"].pov(side)
            scored.append((mv, win_pct(s.score(), s.mate()), inf))
        if not scored:
            continue
        top = scored[0][1]
        tol = 3 if top > 90 or top < 10 else 5
        good = [mv.uci() for mv, w, _ in scored if top - w <= tol]
        if (m["uci"] in good and kind == "yours") or len(good) > 5:
            continue
        evals = {mv.uci(): {"san": b.san(mv), "wp": round(w, 1), "line": b.variation_san(inf["pv"][:6])} for mv, w, inf in scored}
        dests = {}
        for mv in b.legal_moves:
            dests.setdefault(chess.square_name(mv.from_square), []).append(chess.square_name(mv.to_square))
        out.append({"ply": m["ply"], "kind": kind, "fen": fen, "color": "white" if side else "black",
                    "good": good, "evals": evals, "dests": dests, "played": m["san"], "playedCls": m["cls"],
                    "bestSan": evals[good[0]]["san"] if good else m["bestSan"]})
    return out
