# Board Game Buddy

Board Game Buddy is a multi-agent assistant for discovering, understanding, and choosing board games. It can analyze a shelf photo, combine the detected collection with a user's favorites and player count, and produce personalized recommendations.

## Quick start

### 1. Configure Portkey

From this directory:

```bash
cp .env.example .env
```

Put your Portkey key in `.env`:

```text
PORTKEY_API_KEY=your_key_here
```

### 2. Install and run the backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/ in a browser.

## Deploy for phone testing

This repository includes `render.yaml` for deployment on Render. Create a new Web Service from this GitHub repository, keep the generated start command, and enter `PORTKEY_API_KEY` only in Render's private environment-variable field. Do not commit the key to GitHub. Render will provide the shareable HTTPS app URL after the first deploy.

The repository also publishes a browser-only demo through GitHub Pages at `https://janet824.github.io/board-game-buddy/`. The demo is labeled **Demo Mode** and uses sample results; it does not call Portkey or represent live AI output.

### Safety before sharing

- Never commit `.env` or paste the Portkey key into source code, screenshots, prompts, or the README. The real key is loaded only from the local `.env` file, which is ignored by Git.
- `.env.example` contains configuration names only; its blank key is intentional.
- Uploaded photos and questions are processed for the current request and are not saved by this app.
- Do not put private names, addresses, ZIP codes, collection screenshots, or rulebook photos into the repository.
- Purchase and rules links are displayed only when they use HTTPS, come from an approved domain, and pass a reachability check. A failed or untrusted link is suppressed.
- Recommendations exclude games identified as already owned; uncertain ownership is labeled for review.

The first version intentionally uses a small, visible workflow so a grader can run it quickly:

1. Upload a shelf photo.
2. Describe favorite games and typical player count.
3. Review the detected titles.
4. Ask Board Game Buddy for recommendations.

The form also asks which expansions are included and whether the group uses variants or house rules. These are important for rules questions: the Rules Helper must identify the applicable official source and label house-rule interpretations separately.

## Recommendation policy

Recommendations must be for games the player does not already own. The recommender excludes every title identified in the uploaded shelf photo or supplied collection data before generating suggestions. If ownership is uncertain, it asks the player to confirm rather than presenting the game as a new recommendation.

Purchase guidance asks whether the player wants new, used, or either condition. It also asks for optional ZIP-code-based local-store searching consent. The Purchase Advisor provides a verified primary purchase link and secondary value link when available; crowdfunding and publisher-exclusive games link only to their official campaign or publisher page. Local store availability is labeled as needing confirmation unless a store website supplies current inventory.

When a purchase request is ambiguous—such as “Azul” or “Catan”—the advisor pauses and asks whether the user means any version, the base game, or a specific expansion before searching.

If the user selects “any version,” the advisor shows every relevant verified version or expansion it finds and labels each as a base game, standalone edition, or expansion. It does not imply that the list is an exhaustive catalog beyond the sources searched.

Purchase results are displayed as comparison cards so the player can choose an option. Each card includes the store, price, availability, primary/value links, and a call-first reminder when local inventory is not guaranteed.

When relevant, the advisor also adds a third big-box comparison from a trusted retailer such as Target or Walmart.

The Player Count Advisor separates the publisher’s supported range, official rulebook or publisher recommendations, and community-reported optimal counts. It also reports setup and rules friction and whether physical or digital play is likely to be more practical. Community evidence and digital-platform claims are labeled and linked only when a trusted source is available.

When a legitimate digital implementation exists, the advisor recommends its verified official or established platform link. Recognized digital sources include Tabletopia, Board Game Arena, Steam, Apple’s App Store, and Google Play.

## Device support

The interface is responsive for desktop computers and phones. On phones it switches to a single-column layout, keeps controls touch-friendly, supports taking a shelf photo directly from the camera upload control, and respects reduced-motion preferences.

The Rules Helper is a separate live-play workflow. Upload a rulebook/card photo, enter the question, identify expansions, and disclose variants or house rules. The response gives a ruling, numbered steps, evidence notes, and uncertainty notes.

Rules links are restricted to HTTPS sources on recognized publisher, official game, board-game database, or established community domains. Links from unknown hosts are not displayed, even if they respond successfully.

## Agent architecture

`backend/agents/boss.py` is the coordinator. It delegates to focused workers in `backend/agents/workers.py`:

- Shelf interpreter: identifies visible games and shelf-group context
- Recommendation worker: matches games to favorites and collection context
- Player-count worker: evaluates best practical player counts, setup friction, and physical versus digital play

Rules and new-release workers remain extension points for the next iteration. The API keeps worker results separate so additional research workers can be added without changing the user interface.

## Component identities

The interface represents each worker as a board-game component:

- Shelf Interpreter: hex tiles fitting together
- Game Recommender: dice clattering
- Player Count Advisor: transparent cubes stacking

Click **Enable agent sounds** to hear the optional procedural completion sounds. The browser requires this explicit opt-in before audio can play.
