"""Game phase segmentation: setup / opening / tactics / endgame.

A game is split into consecutive segments so the UI can always say which part of
the game you are looking at.  Two sources are supported:

* :func:`from_sans` - a cheap estimate from the moves alone (no engine).  Used by
  the library list so every indexed game can be labelled.
* :func:`from_moves` - the engine-refined version used after a full analysis,
  where the evaluation swing is known as well.
"""
import chess

VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
PHASES = ("setup", "opening", "tactics", "endgame")
# The stages a game actually goes through, named the way players name them.
LABEL = {"setup": "Opening Book", "opening": "Early Game", "tactics": "Middlegame", "endgame": "Endgame"}
BLURB = {
    "setup": "theory - moves straight out of the opening book",
    "opening": "early game - development and plans, no crisis yet",
    "tactics": "middlegame - sharp positions and critical moments",
    "endgame": "endgame - few pieces left, technique decides",
}


def heavy_material(board):
    """Value of the non-pawn, non-king material still on the board."""
    return sum(VAL[p.piece_type] for p in board.piece_map().values()
               if p.piece_type not in (chess.PAWN, chess.KING))


def has_queens(board):
    return bool(board.pieces(chess.QUEEN, chess.WHITE) or board.pieces(chess.QUEEN, chess.BLACK))


def endgame_ply(board):
    """The ply at which this position already counts as an endgame (else None)."""
    heavy = heavy_material(board)
    if heavy <= 26 or (not has_queens(board) and heavy <= 34):
        return True
    return False


def _clusters(plies, gap):
    """Group sorted plies into runs where neighbours are at most `gap` apart."""
    out = []
    for p in plies:
        if out and p - out[-1][1] <= gap:
            out[-1][1] = p
        else:
            out.append([p, p])
    return out


def build(nplies, setup_end=0, tactics=(), endgame_start=None, gap=6):
    """Segments covering plies 1..nplies.

    setup_end    - last ply that is still book/setup theory
    tactics      - plies that mark a critical moment (captures, big eval swings)
    endgame_start- first ply of the endgame (overrides everything)
    """
    if nplies <= 0:
        return []
    phases = [None] * (nplies + 1)
    if endgame_start is not None:
        for p in range(max(1, endgame_start), nplies + 1):
            phases[p] = "endgame"
    setup_end = max(0, min(int(setup_end or 0), nplies))
    for p in range(1, setup_end + 1):
        if phases[p] is None:
            phases[p] = "setup"
    for a, b in _clusters(sorted(set(int(t) for t in tactics)), gap):
        lo = max(a, setup_end + 1)
        hi = min(nplies, b + gap)
        for p in range(lo, hi + 1):
            if phases[p] is None:
                phases[p] = "tactics"
    for p in range(1, nplies + 1):
        if phases[p] is None:
            phases[p] = "opening"
    segs = []
    for p in range(1, nplies + 1):
        if segs and segs[-1]["phase"] == phases[p]:
            segs[-1]["to"] = p
        else:
            segs.append({"phase": phases[p], "from": p, "to": p})
    return _tidy(segs)


def _tidy(segs):
    """Fold away flickers: a short opening gap between two tactics runs is tactics."""
    out = [dict(s) for s in segs]
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(1, len(out) - 1):
            s = out[i]
            if s["phase"] == "opening" and (s["to"] - s["from"] + 1) <= 3 and out[i - 1]["phase"] == "tactics" and out[i + 1]["phase"] == "tactics":
                s["phase"] = "tactics"
                changed = True
        # merge neighbours of the same phase
        merged = [out[0]]
        for s in out[1:]:
            if merged[-1]["phase"] == s["phase"]:
                merged[-1]["to"] = s["to"]
            else:
                merged.append(s)
        out = merged
    return out


def describe(segments, nplies=None):
    """One-line summary such as 'Setup (1-8) -> Opening (9-16) -> Tactics (17-40)'."""
    if not segments:
        return "No phases"
    parts = []
    for s in segments:
        span = f"{s['from']}-{s['to']}" if s["to"] > s["from"] else str(s["from"])
        parts.append(f"{LABEL.get(s['phase'], s['phase'])} ({span})")
    return " → ".join(parts)


def summary(segments):
    """Compact structure for the UI."""
    return {
        "phases": [{"phase": s["phase"], "label": LABEL.get(s["phase"], s["phase"]), "from": s["from"], "to": s["to"]}
                   for s in segments],
        "names": [s["phase"] for s in segments],
        "text": describe(segments),
        "has": sorted({s["phase"] for s in segments}),
    }


def phase_at(segments, ply):
    """Which phase a given ply (1-based) belongs to."""
    for s in segments:
        if s["from"] <= ply <= s["to"]:
            return s["phase"]
    return segments[-1]["phase"] if segments else "opening"


def from_sans(sans, setup_end=0):
    """Estimate the phases from a SAN move list, without the engine.

    A ply only starts the tactical phase when a real piece changes hands (the
    ordinary pawn exchanges of the opening do not count) or a promotion happens.
    """
    board = chess.Board()
    tactics, end_at = [], None
    nplies = 0
    for i, san in enumerate(sans, 1):
        try:
            mv = board.parse_san(san)
        except Exception:  # noqa: BLE001 - a bad token just ends the estimate
            break
        before = heavy_material(board)
        promo = mv.promotion is not None
        board.push(mv)
        nplies = i
        if promo or heavy_material(board) - before <= -3:
            tactics.append(i)
        if end_at is None and endgame_ply(board):
            end_at = i
    segs = build(nplies, setup_end=setup_end, tactics=tactics, endgame_start=end_at)
    return {"segments": segs, **summary(segs)}


def from_ucis(ucis, setup_end=0):
    """Phases from a UCI move list - same result as :func:`from_sans` but much
    faster, because the moves are replayed instead of parsed out of SAN.

    Used for library games, where the whole game is on disk but only the first
    40 plies of SAN were kept in the index.
    """
    board = chess.Board()
    tactics, end_at = [], None
    nplies = 0
    for i, uci in enumerate(ucis, 1):
        try:
            mv = chess.Move.from_uci(uci)
        except Exception:  # noqa: BLE001
            break
        if mv not in board.legal_moves:
            break
        before = heavy_material(board)
        promo = mv.promotion is not None
        board.push(mv)
        nplies = i
        if promo or heavy_material(board) - before <= -3:
            tactics.append(i)
        if end_at is None and endgame_ply(board):
            end_at = i
    segs = build(nplies, setup_end=setup_end, tactics=tactics, endgame_start=end_at)
    return {"segments": segs, **summary(segs)}


def from_moves(moves, setup_end=0, start_fen=None, start_wp=None):
    """Engine-refined phases for an analysed game.

    `moves` is the list produced by :func:`gr.analysis.analyze` (each entry has
    fen, wp, ...).  A ply counts as tactics when material came off the board or
    the evaluation swung hard.
    """
    if not moves:
        segs = build(0)
        return {"segments": segs, **summary(segs)}
    board = chess.Board(start_fen) if start_fen else chess.Board()
    prev_wp = start_wp
    tactics, end_at = [], None
    nplies = len(moves)
    for m in moves:
        ply = m["ply"]
        try:
            after = chess.Board(m["fen"])
        except Exception:  # noqa: BLE001
            break
        lost = heavy_material(board) - heavy_material(after)
        if lost >= 3:
            tactics.append(ply)
        if prev_wp is not None and m.get("wp") is not None:
            if abs(m["wp"] - prev_wp) >= 10:
                tactics.append(ply)
            prev_wp = m["wp"]
        if end_at is None and endgame_ply(after):
            end_at = ply
        board = after
    segs = build(nplies, setup_end=setup_end, tactics=tactics, endgame_start=end_at)
    return {"segments": segs, **summary(segs)}
