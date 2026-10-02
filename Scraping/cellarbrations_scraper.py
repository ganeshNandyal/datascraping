import csv
import json
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from typing import Any


try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sync_playwright = None
    PlaywrightTimeout = Exception

BASEURL = "https://www.cellarbrations.com.au"
CATEGORY_URL = f"{BASEURL}/sm/delivery/rsid/144981/categories/spirits/whisky-id-Whisky_Food"

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0 Safari/537.36"
)

SIZE_REGEX = re.compile(
    r"(?P<units>\d+(?:\.\d+)?)\s*(?P<unit>m[lL]|[lL]|ml|ML|g|kg|cl|oz)\b",
    re.IGNORECASE,
)

def converting_size(text):
    if not text:
        return "", ""
    m = SIZE_REGEX.search(text)
    if not m:
        return "" , ""
    units=m.group("units")
    unit=m.group("unit")
    unit_norm = unit[0].upper()+unit[1:].lower() if len(unit)>1 else unit.upper()
    if unit_norm.lower() in ("ml","ml."):
        unit_norm="ML"
    elif unit_norm.lower() == "l":
        unit_norm="L"
    return unit_norm, units

def scrape_using_playwright(max_products, fetch_descriptions):

    if sync_playwright is None:
        raise RuntimeError(
            "Playwright is not installed. Run pip install playwright && playwright install chromium"
        )
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    product_rows = []

    with sync_playwright() as p :
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENTS,
            locale="en-AU",
            viewport={"height":900,"width":1280}
        )
        page = context.new_page()

        print(f"Navigating to category: {CATEGORY_URL}", file=sys.stderr)
        try:
            page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=60000)
            # Allow Cloudflare / JS to settle
            page.wait_for_timeout(4000)
        except PlaywrightTimeout:
            print("Timeout loading category page", file=sys.stderr)
            browser.close()
            return product_rows
        page.wait_for_timeout(2500)

        for _ in range(5):
            page.evaluate("window.scrollBy(0,window.innerHeight)")
            page.wait_for_timeout(1000)

        products_js = page.evaluate(
            """() => {
            const items = [];
            // Common e-commerce card patterns
            const cards = document.querySelectorAll(
              '[data-testid*="product"], [class*="product-card"], [class*="ProductCard"], article, .product, [class*="tile"]'
            );
            const seen = new Set();
            for (const card of cards) {
              const link = card.querySelector('a[href*="/product"], a[href*="/products"], a[href*="pid"]')
                || card.closest('a') || card.querySelector('a');
              if (!link) continue;
              const href = link.href || link.getAttribute('href') || '';
              if (!href || seen.has(href)) continue;
              // Skip non-product navigation
              if (/\\/(categories|cart|account|login)/i.test(href)) continue;
              seen.add(href);

              const nameEl = card.querySelector('h2, h3, [class*="name"], [class*="title"], [data-testid*="name"]');
              const priceEl = card.querySelector('[class*="price"], [data-testid*="price"], .price');
              const imgEl = card.querySelector('img');

              const name = (nameEl && nameEl.textContent || link.textContent || '').trim().replace(/\\s+/g, ' ');
              const price = (priceEl && priceEl.textContent || '').trim().replace(/\\s+/g, ' ');
              const image = imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '';

              // Try to find a product id in URL or data attributes
              let pid = card.getAttribute('data-product-id')
                || card.getAttribute('data-id')
                || '';
              if (!pid) {
                const m = href.match(/(?:product[_-]?id|pid|id)=([\\w-]+)/i)
                  || href.match(/\\/products?\\/([\\w-]+)/i)
                  || href.match(/-(\\d{5,})(?:\\/|$)/);
                if (m) pid = m[1];
              }

              if (name || href) {
                items.push({ name, price, image, url: href, product_id: pid });
              }
            }
            // Fallback: any product-looking links if cards yielded little
            if (items.length < 3) {
              document.querySelectorAll('a[href*="product"]').forEach(a => {
                const href = a.href;
                if (seen.has(href)) return;
                seen.add(href);
                const name = (a.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
                if (name.length > 5) {
                  items.push({ name, price: '', image: '', url: href, product_id: '' });
                }
              });
            }
            return items;
            }"""
        )

        print(f"Found {len(products_js)} product-like items on listing", file=sys.stderr)
        
        if max_products is not None:
            products_js = products_js[:max_products]
        for i,item in enumerate(products_js):
            product_name=item.get("name") or ""
            product_url = item.get("url") or ""
            if product_url and not product_url.startswith("http"):
                product_url=urljoin(BASEURL, product_url)
            product_id=str(item.get("product_id") or "")
            product_image=item.get("image") or "" 
            product_price = item.get("price") or ""
            description = ""
            if fetch_descriptions and product_url:
                try:
                    page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    description = page.evaluate(
                        """() => {
                        const sel = document.querySelector(
                          '[class*="description"], [data-testid*="description"], .product-description, #description, [itemprop="description"]'
                        );
                        return sel ? sel.textContent.trim().replace(/\\s+/g, ' ').slice(0, 2000) : '';
                        }"""
                    ) or ""
                    # Also try to improve price / size from product page
                    extra = page.evaluate(
                        """() => {
                        const priceEl = document.querySelector('[class*="price"], [itemprop="price"], .price');
                        const sizeEl = document.querySelector('[class*="size"], [class*="volume"], [class*="pack"]');
                        return {
                          price: priceEl ? priceEl.textContent.trim() : '',
                          size: sizeEl ? sizeEl.textContent.trim() : ''
                        };
                        }"""
                    )
                    if extra and extra.get("price") and not price:
                        price = extra["price"]
                    size_hint = (extra or {}).get("size") or product_name
                except Exception as e:
                    print(f"  [{i+1}] description fetch failed for {product_url}: {e}", file=sys.stderr)
                    size_hint = product_name
            else:
                size_hint = product_name

            measuring_unit, units = converting_size(size_hint)
            if not measuring_unit:
                measuring_unit,units = converting_size(product_name)
            product_rows.append({
                "product_id": product_id,
                "product_name": product_name,
                "product_price": product_price,
                "product_url": product_url,
                "product_image_url": json.dumps([product_image] if product_image else []),
                "product_unit": units,
                "product_measuring_unit": measuring_unit,
                "product_scraped_at": scraped_at,
                "product_description": description,
            })

            print(f" [{i+1}/{len(products_js)}] {product_name[:60]!r}", file=sys.stderr)

        browser.close()
    return product_rows


def converting_to_csv(product_rows,path:Path):
    """
    Convert product rows to a CSV file.

    Args:
        product_rows (list): List of product rows.
        path (Path): Path to save the CSV file.
    """
    fieldsnames = [
        "product_id",
        "product_name",
        "product_price",
        "product_url",
        "product_image_url",
        "product_unit",
        "product_measuring_unit",
        "product_scraped_at",
        "product_description"
    ]

    path.parent.mkdir(parents=True, exist_ok = True)
    with open(path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldsnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(product_rows)

def main():
    parser = argparse.ArgumentParser(
        description="Scrape product information from Cellarbrations  wishky category"
    )

    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="Limit number of products (useful for testing)"
    )
    parser.add_argument(
        "--no-descriptions",
        action="store_true",
        help="Skip visting individual product pages",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cellabrations_whisky.csv"),
        help="Output CSV PATh",
    )

    args= parser.parse_args()
    print(
    "NOTE Cellearbrations is Cloudflate-protected. "
    "This scraper uses PlayWright (browser). See notes.txt.",
    file=sys.stderr,
    )

    try:
        product_rows = scrape_using_playwright(
            max_products=args.max_products,
            fetch_descriptions=not args.no_descriptions,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if not product_rows:
        print(
            "No products were extracted. The listing may still be blocked by Cloudflare "
            "or the page structure may have changed; no CSV was written.",
            file=sys.stderr,
        )
        return 1

    converting_to_csv(product_rows, args.output)
    print(f"Wrote {len(product_rows)} rows to {args.output}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
    