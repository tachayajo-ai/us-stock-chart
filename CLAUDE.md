# CLAUDE.md — US Stock Chart

Notes for Claude sessions working on this repo. The user-facing Thai manual is `MANUAL.md`.

## User
- Thai nurse anesthetist, non-programmer, mostly on a tablet/phone. Reply in Thai, concise, step-by-step with exact taps/links.
- Prefers Claude to commit and push directly to `main` (Claude GitHub App is installed). Never ask them to upload files.
- After a push: Streamlit Cloud redeploys in 1–2 min; tell them to refresh the page (Reboot app if new config/deps).

## Architecture
- **App**: Streamlit, deployed at `tachaya-stock.streamlit.app` (Streamlit Community Cloud, repo `tachayajo-ai/us-stock-chart`, main file `app.py`).
- **Data source**: `yfinance` (Yahoo). Buffett Indicator from **DBnomics** (Fed Z.1 `FL103164105.Q` ÷ BEA GDP `A191RC-Q`) — FRED blocks cloud IPs.
- **Persistence**: JSON files in this repo written via GitHub Contents API using Streamlit secrets `GITHUB_TOKEN`, `GITHUB_REPO` (`read_repo_json` / `write_repo_json` in `app.py`; local file fallback when no secrets).
- **Automation (GitHub Actions)**: `screener.yml` (Sat 03:17 UTC; S&P 500 + Nasdaq-100 → `screener.csv` with `indexes` column, `buffett_indicator.csv`), `screener_r1000.yml` (Sun 03:17 UTC; `--universe r1000`: "Top 1000" = 1,000 largest US-listed companies by market cap via `yf.screen` (≈ Russell 1000; iShares blocks scripts, Wikipedia has no list) minus tickers already in screener.csv → `screener_r1000.csv`, member list `russell1000.json`; app's `load_screener()` merges all three), `alerts.yml` (every 30 min weekdays; ntfy push; writes `alerts_state.json`; secret `NTFY_TOPIC`). Claude can trigger them with `gh workflow run <file> -R tachayajo-ai/us-stock-chart --ref main [-f test=true]`.

## Files
| File | Purpose |
|---|---|
| `app.py` | All UI. Tabs: Data (label "📈 Data"; ticker selectbox `chart_ticker` at top; then a second `with tab_chart:` appends company info [profile TH/EN + earnings in expanders] → Buffett checklist/DCF/annual financials → news last), Watchlist (add/select/reorder ⏫↑↓/delete via callbacks in horizontal containers; no sidebar — `pick()` callback sets `selected` + `chart_ticker`), เปรียบเทียบ, Screener (market-wide Buffett Indicator appended at the bottom via a second `with tab_screener:`), แจ้งเตือน, พอร์ต, คู่มือ |
| `buffett.py` | Fundamentals fetch, Buffett checklist, Owner Earnings (OCF − SBC − maintenance capex, Greenwald), DCF, implied growth, financials detection, P/B, share count, Buffett Indicator |
| `screener.py` | Screens: main = S&P 500 (Wikipedia) + Nasdaq-100, r1000 = Top 1000 via `yf.screen` · Nasdaq-100 from Wikipedia list page → api.nasdaq.com |
| `company.py` | Google Translate (free gtx endpoint; optional `GOOGLE_TRANSLATE_API_KEY`), news (Yahoo → Google News RSS), earnings |
| `alerts.py` | Alert model + checker + ntfy JSON publish |
| `portfolio.py` | Transactions, average cost, P/L, dividends (15% Thai withholding), value vs SPY, Fernet+PBKDF2 encryption, multi-portfolio `{"portfolios":[...]}` |
| `guides.py` | 📘 interpretation texts shown in the app |
| `ui.py` | CSS (dark broker style) + hero/chips HTML; `.streamlit/config.toml` theme |
| `ai-analyst/` | Claude Skill `stock-analyst` (SKILL.md + 13 references) and its zip |
| data | `watchlist.json`, `alerts.json`, `alerts_state.json`, `screener.csv`, `screener_r1000.csv`, `russell1000.json`, `buffett_indicator.csv`, `portfolio.enc.json` (encrypted; password only the user knows) |

## Gotchas (learned the hard way)
- Streamlit Cloud does not reload imported modules after a push → `app.py` reloads `buffett, guides, company, alerts, portfolio, ui` via `importlib.reload`. Add new modules to that list.
- `st.date_input` front-end chunk fails to load on the deployed app → use `date_picker()` (day/month/year selectboxes).
- Two `$` in one Markdown string render as LaTeX → wrap with `esc_dollar()`. `md_safe()` also replaces `[ ]`.
- Do not set `st.session_state[key]` after its widget is created (use a `*_next` key consumed before the widget).
- yfinance quirks: `priceToBook` wrong for multi-class shares (use `price_to_book()`), `sharesOutstanding` per class (use `share_count()` = marketCap/price), `Change In Working Capital` unreliable (Owner Earnings uses OCF).
- Financials (banks/insurers/brokers/lenders by Yahoo `industry`; V/MA/PYPL excluded) get no DCF/MOS; show P/B + ROE.
- DCF default growth is conservative: 0% if any negative cash-flow year, else min(cash-flow CAGR on 2-yr rolling mean, revenue CAGR), capped 10%.
- Watchlist must keep ≥1 item (empty list used to stop the whole app).
- This sandbox cannot reach Yahoo/FRED/ntfy; test with a mocked `yfinance` (`sys.modules["yfinance"] = SimpleNamespace(Ticker=Fake, download=...)`) via `streamlit.testing.v1.AppTest`, and visually with `streamlit run` + Playwright screenshots. Verify real data by reading committed CSVs or Actions logs (`gh run view <id> --log`).
- `git push` can race with the Actions bot: `git fetch && git rebase origin/main` before pushing; retry.

## Conventions
- Commit messages in English; end with the session attribution lines provided by the environment.
- Keep UI text Thai; update `MANUAL.md` (and `guides.py` if relevant) when behavior changes.
