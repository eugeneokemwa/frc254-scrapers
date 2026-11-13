# Kenyan chess games: scrapers

GitHub Actions jobs that collect the games of Kenyan FIDE-rated players from public sources for a Kenyan chess site:
chess-results.com tournament histories and PGNs (`tools/scrape-*.py`) and LiveChessCloud live boards (`tools/fetch-livechesscloud.py`).
Run **Games history** from the Actions tab; the results are workflow artifacts. Also: **Games abroad** (our players' games in tournaments abroad), **FIDE history** (monthly ratings since 2020 for ratings at the time of each game) and **Game reviews** (Stockfish reviews of the site's games, `assets/games/lib`).

The scrapers are polite (one request at a time per runner, pauses, stop when refused) and keep each runner far below chess-results'
daily limit. `assets/` holds the public player lists they start from; `tools/cache/cr-tnr/` the tournaments already collected.
