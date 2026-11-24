# Board Game Buddy

Board Game Buddy helps a player understand a rules question, find games that fit the collection they already love, and compare one online price, one local shop, and one big-box price before buying.

Author: Janet Leung

- Repository: https://github.com/janet824/board-game-buddy
- Browser-only demo (sample results, no live model): https://janet824.github.io/board-game-buddy/

The graded app is this local server. It calls OpenAI with `gpt-6-luna` only. The GitHub Pages link is a labeled demo and does not call the model.

## Run it

Use Python 3.11 or newer. From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

On Windows, activate the virtual environment with `.venv\Scripts\activate`.

Then open http://127.0.0.1:8000/

### API key

The app reads `OPENAI_API_KEY` from a `.env` file. It looks in this folder first, then in the folder above it. A local file wins when both exist. Copy `.env.example` if you need a template:

```text
OPENAI_API_KEY=your_key_here
MODEL_NAME=gpt-6-luna
```

Do not commit a real key. When the teaching staff clones this repo into a submissions folder, their shared `.env` one level above the repo is enough.

Check that the server is up:

```bash
curl http://127.0.0.1:8000/api/health
```

The response includes `"model": "gpt-6-luna"`.

## What to try

The home page has three tasks. Each one keeps what you typed until you press **Clear this page**. A phone can lock while a search is running; the result comes back when you reopen the page.

1. **Explore my shelf.** A photo is optional. Type favorite games and the usual player count, or note that a shelf area holds favorites. The app returns five games that fit those favorites and five different newer games. Owned games are left out. Each suggestion says whether it is better online, with a platform link, or better to buy and play in person.
2. **Solve a rules question.** Type the game name and the question. A card or rulebook photo is optional. The helper looks up the official rulebook, a publisher FAQ when one exists, and an unofficial source such as BoardGameGeek.
3. **Find where to buy.** Search one game at a time. The page gives a player-count and setup note, then one online listing, one local shop when a ZIP code and permission are provided, and one Target or Walmart listing. Local stock should be confirmed by a phone call.

If a name such as Azul or Catan could mean a base game or an expansion, the purchase search asks which one before it looks up prices.

## Agents

`backend/agents/boss.py` coordinates the shelf and purchase tasks.

- Shelf Interpreter reads a shelf photo and marks favorites when the note says an area holds them.
- Games for you and New games come from one recommendation call. Code then removes owned games and keeps an online link only when setup is heavy and the platform is real.
- Player Count Advisor and Purchase Advisor run together when you search a game to buy. Purchase research uses web search. Prices ignore shipping, cart, and pickup text, and search-page links are dropped.
- Rules Helper is a separate task in `backend/agents/rules.py`. It searches the rulebook first, then writes a structured answer. Links that are not HTTPS, or that use a shortener, are not shown.

## Safety

- The key stays in `.env`. It is not written into source, the README, or saved uploads.
- Uploaded photos are used for the current request and are not stored.
- Player count must be from 1 to 20.
- Purchase and rules links are checked before they are shown.
- A local store is searched only after the player enters a ZIP code and checks the permission box.
