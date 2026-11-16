#!/usr/bin/env python3
"""Self-test: analyze() must not crash on a PGN that plays on after the game is already over (fivefold repetition). Stub engine, no Stockfish."""
import io, os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "chesslab"))
import chess, chess.engine, chess.pgn
from gr import analysis
class Eng:
    def configure(self, o): pass
    def quit(self): pass
    def analyse(self, board, limit, multipv=1):
        return [{"score": chess.engine.PovScore(chess.engine.Cp(10), board.turn), "pv": [m], "multipv": k + 1} for k, m in enumerate(list(board.legal_moves)[:multipv])]
analysis.chess.engine.SimpleEngine = types.SimpleNamespace(popen_uci=lambda p: Eng())
analysis.engine_path = lambda: "stub"
shuffle = "Nf3 Nf6 Ng1 Ng8 " * 5 + "Nf3 Nf6"   # fivefold ends the game at ply 16; the PGN goes on
game = chess.pgn.read_game(io.StringIO(" ".join(f"{i // 2 + 1}. {m}" if i % 2 == 0 else m for i, m in enumerate(shuffle.split())) + " 1/2-1/2"))
review, eng = analysis.analyze(game, "quick")
assert len(review["moves"]) == 22, len(review["moves"])
print("ok")
