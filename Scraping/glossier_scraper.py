import argparse
import re
import requests
import json
import sys
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

BASEURL = "https://www.glossier.com"
COLLECTION_JSON = f"{BASEURL}/collections/all/products.json"
PRODUCTS_JSON = f"{BASEURL}/products/{{handle}}.json"

LIMIT = 250

# FIXED: Added missing space in User-Agent string (was concatenated without space)
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD",
    "IN": "INR",
    "GB": "GBP",
    "AU": "AUD",
    "JP": "JPY",
    "FR": "EUR",
}


def get_currency(country_code: str) -> str:
    """
    Get the currency for a given country code.

    Args:
        country_code (str): The country code.
    """
    country_code = country_code.strip().upper()

    if country_code not in COUNTRY_CURRENCY:
        raise ValueError(f"Invalid country code: {country_code}")

    return COUNTRY_CURRENCY[country_code]


def creating_session(country_code: str | None = None) -> requests.Session:
    """
    Create a requests session with custom headers.

    Returns:
        requests.Session: A configured requests session.
    """
    session = requests.Session()
    # FIXED: Removed extra space in Accept-Language fallback
    language = f"en-{country_code},en;q=0.9" if country_code else "en-US,en;q=0.9"
    session.headers.update(
        {
            "User-Agent": USER_AGENTS,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": language,
        }
    )
    if country_code:
        currency = get_currency(country_code)
        # FIXED: Cookie domain should be "www.glossier.com" (more reliable)
        # FIXED: Cookie names closer to what Shopify actually uses
        session.cookies.set("localisation", country_code.upper(), domain="www.glossier.com")
        session.cookies.set("currency", currency, domain="www.glossier.com")

    return session


def get_products_data(session: requests.Session, country_code: str | None = None) -> List[Dict[str, Any]]:
    """
    Fetch product data from the Glossier API.

    Args:
        session (requests.Session): The requests session.
        country_code (str | None): The country code for localization.
    """
    products_data: list[Dict[str, Any]] = []
    page = 1
    currency = get_currency(country_code) if country_code else None

    while True:
        params: Dict[str, Any] = {"page": page, "limit": LIMIT}
        if country_code and currency:
            # FIXED: Shopify Markets prefers "country" not "country_code"
            params["country"] = country_code.upper()
            params["currency"] = currency
        # ADDED: timeout to avoid hanging forever
        response = session.get(COLLECTION_JSON, params=params, timeout=30)
        response.raise_for_status()
        data = response.json().get("products") or []

        if not data:
            break

        products_data.extend(data)
        if len(data) < LIMIT:
            break
        page += 1
    return products_data


def remove_html_Tags(html: str | None) -> str:
    """
    Remove HTML tags from a string.

    Args:
        html (str | None): The input string containing HTML.
    """
    if not html:
        return ""

    # FIXED: Slightly better regex (non-greedy + handles attributes better)
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def parse_product_data(
    product_data: Dict[str, Any],
    scraped_at: str,
    expand_variants: bool,
) -> List[Dict[str, Any]]:   # FIXED: Return type is always List, not Dict
    """
    Parse product data and extract relevant information.
    If expand_variants is False: one row per product (first variant price).
    If True: one row per variant (variant treated as unique product).
    """

    product_name = product_data.get("title")
    product_id = product_data.get("id")
    handle = product_data.get("handle")
    url = f"{BASEURL}/products/{handle}" if handle else ""
    # FIXED: Safer handling if "images" key is missing
    images = [img.get("src") for img in (product_data.get("images") or []) if img.get("src")]
    description = remove_html_Tags(product_data.get("body_html"))
    variants = product_data.get("variants") or []

    if not expand_variants or not variants:
        price = ""
        if variants:
            price = str(variants[0].get("price") or "")
        return [
            {
                "product_id": product_id,
                "product_name": product_name,
                "product_url": url,
                "product_images": json.dumps(images),
                "product_price": price,
                "product_description": description,
                "scraped_at": scraped_at,
            }
        ]

    # ========== CRITICAL BUG FIX START ==========
    # Original code had the append() indented under the featured_image if-block.
    # That meant any variant WITHOUT a featured_image was completely skipped.
    # Also product_name was being mutated across loop iterations.
    # ========== CRITICAL BUG FIX END ==========

    product_rows = []
    for v in variants:
        variant_id = v.get("id")
        variant_title = v.get("title")

        # FIXED: Do not mutate the original product_name
        if variant_title and variant_title.lower() != "default title":
            name = f"{product_name} - {variant_title}"
        else:
            name = product_name

        variant_images = images[:]  # FIXED: Make a copy
        featured_image = v.get("featured_image")
        if isinstance(featured_image, dict) and featured_image.get("src"):
            src = featured_image["src"]
            variant_images = [src] + [i for i in images if i != src]

        # FIXED: append is now always executed (was previously inside the if)
        product_rows.append(
            {
                "product_id": str(variant_id or product_id),
                "product_name": name,
                "product_url": url,
                "product_images": json.dumps(variant_images),
                "product_price": str(v.get("price") or ""),
                "product_description": description,
                "scraped_at": scraped_at,
            }
        )
    return product_rows


def save_to_csv(product_rows: list[Dict[str, Any]], path: Path) -> None:
    """
    Save product data to a CSV file.

    Args:
        product_rows (list[Dict[str, Any]]): The product data rows.
        path (Path): The path to the output CSV file.
    """
    if not product_rows:
        print("No data to save.")
        return

    fieldnames = [
        "product_id",
        "product_name",
        "product_url",
        "product_images",
        "product_price",
        "product_description",
        "scraped_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(product_rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape product data from Glossier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--variants",
        action="store_true",
        help="Expand variants as unique products.",
    )

    parser.add_argument(
        "--country",
        type=str,
        default=None,
        metavar="COUNTRY_CODE",
        help=(
            "Country code for localization (e.g., US, IN, GB). "
            "Currency will be set accordingly. If not provided, defaults to USD."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/glossier_products.csv"),
        metavar="OUTPUT_PATH",
        help="Output CSV file path. Defaults to 'data/glossier_products.csv'.",
    )
    args = parser.parse_args()

    country_code = args.country.upper().strip() if args.country else None
    if country_code:
        try:
            currency = get_currency(country_code)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
    else:
        currency = None

    # FIXED: Variable name casing (was scraped_At)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    session = creating_session(country_code)

    print(f"Fetching products from {COLLECTION_JSON} ...", file=sys.stderr)

    if country_code and currency:
        print(f"Country: {country_code}, Currency: {currency}", file=sys.stderr)

    # FIXED: Variable name casing (was products_Data)
    products_data = get_products_data(session, country_code)

    print(f"Fetched {len(products_data)} products.", file=sys.stderr)

    product_rows = []
    for product in products_data:
        product_rows.extend(
            parse_product_data(product, scraped_at, expand_variants=args.variants)
        )
    save_to_csv(product_rows, args.output)

    print(f"Saved {len(product_rows)} product rows to {args.output}.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())