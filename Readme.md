# Data Engineer Scraping Test

Python command-line scrapers for the Euromonitor take-home assignment. Each exercise writes a CSV.

| Exercise | Target | Approach |
|----------|--------|----------|
| **1** (required) | [Glossier – All products](https://www.glossier.com/collections/all) | Shopify `products.json` over plain HTTP |
| **2** (stretch; required for Data Engineer) | [Cellarbrations – Whisky](https://www.cellarbrations.com.au/sm/delivery/rsid/144981/categories/spirits/whisky-id-Whisky_Food) | Playwright (Cloudflare blocks plain HTTP) |

See `notes.txt` for decisions, tradeoffs, Task 5 evidence, and AI disclosure.

---

## Setup

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt