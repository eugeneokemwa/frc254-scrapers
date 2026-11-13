"""Opening names from the Lichess chess-openings list (data/openings.tsv)."""
import io
import os
import re

import chess
import chess.pgn

_TABLE = None
_PREFIX = None
_MOVE_NO = re.compile(r"^\d+\.*\.?$")


def _epd(board):
    return board.epd()


def _tsv_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "openings.tsv")


def _norm(tok):
    tok = tok.strip()
    if not tok:
        return ""
    tok = re.sub(r"^\d+\.*", "", tok)
    if not tok:
        return ""
    return tok.replace("0-0-0", "O-O-O").replace("0-0", "O-O")


def _prefix_table():
    """SAN prefix -> (eco, name, ply), built from the TSV without parsing PGNs."""
    global _PREFIX
    if _PREFIX is not None:
        return _PREFIX
    _PREFIX = {}
    path = _tsv_path()
    if not os.path.exists(path):
        return _PREFIX
    with open(path, encoding="utf-8") as fh:
        for row in fh:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            eco, name, pgn = parts[:3]
            toks = [_norm(t) for t in pgn.split()]
            toks = [t for t in toks if t and not _MOVE_NO.match(t)]
            if not toks:
                continue
            _PREFIX[tuple(toks)] = (eco, name, len(toks))
    return _PREFIX


def name_for_sans(sans):
    """Return (eco, name, ply) for a SAN move list, matched on the longest known prefix.

    Cheap: no chess parsing, so it can be used to label every game in a list.
    """
    table = _prefix_table()
    best = ("", "Unknown opening", 0)
    sans = list(sans)
    for n in range(min(len(sans), 16), 0, -1):
        hit = table.get(tuple(sans[:n]))
        if hit:
            best = hit
            break
    return best


def eco_names():
    """eco -> most common opening name (for games that only carry an ECO code)."""
    names = {}
    for eco, name, _ply in _prefix_table().values():
        names.setdefault(eco, name)
    return names


_POSITIONS = None


def positions():
    """name -> {eco, fen, moves} for every opening in the list.

    The position is the one the opening's main line reaches - the very place a
    game of that opening sits in, so it makes a good picture for the opening.
    """
    global _POSITIONS
    if _POSITIONS is not None:
        return _POSITIONS
    best = {}
    for moves, (eco, name, _ply) in _prefix_table().items():
        cur = best.get(name)
        if cur is None or len(moves) > len(cur[0]):
            best[name] = (moves, eco)
    out = {}
    for name, (moves, eco) in best.items():
        board = chess.Board()
        try:
            for t in moves:
                board.push(board.parse_san(t))
        except ValueError:
            continue
        out[name] = {"eco": eco, "fen": board.fen(), "moves": list(moves)}
    _POSITIONS = out
    return out


def _load():
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    _TABLE = {}
    path = _tsv_path()
    if not os.path.exists(path):
        return _TABLE
    with open(path, encoding="utf-8") as fh:
        for row in fh:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            eco, name, pgn = parts[:3]
            game = chess.pgn.read_game(io.StringIO(pgn))
            board = game.end().board()
            _TABLE[_epd(board)] = (eco, name)
    return _TABLE


def name_for(moves):
    """Return (eco, name, ply) of the deepest known opening along a move list."""
    table = _load()
    board = chess.Board()
    found = ("", "Unknown opening", 0)
    for i, move in enumerate(moves, 1):
        board.push(move)
        hit = table.get(_epd(board))
        if hit:
            found = (hit[0], hit[1], i)
    return found
