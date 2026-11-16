"""
International Cost of Capital App
---------------------------------
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data (fetched live, cached, with a Refresh button):
  * Industry unlevered betas  - Aswath Damodaran, betas.xls (US industries)
  * BBB (Baa2) default spread - Aswath Damodaran, ratings.html (large non-financial firms)
  * Corporate tax rates       - OECD Corporate Tax Statistics (combined statutory CIT rate)
  * 10-year government yields - Trading Economics, /bonds

Core formulas (all rates are decimals):
  D/E          = w_d / (1 - w_d)
  beta_L       = beta_U * [1 + (1 - t) * D/E]                  (Hamada)
  k_e(USD)     = y_US + beta_L * ERP                              (CAPM)
  k_d(USD)     = y_US + s_USD
  k_e(FC)      = (1 + k_e(USD)) * (1 + y_FC) / (1 + y_US) - 1     (yield-differential / Fisher conversion)
  F            = (1 + y_FC) / (1 + y_US)                          (conversion factor)
  s_FC         = s_USD * F                                        (auto; or manual override)
  k_d(FC)      = y_FC + s_FC     [identical to (1 + k_d(USD)) * F - 1]
  WACC         = (1 - w_d) * k_e + w_d * k_d * (1 - t)
The same tax rate t (U.S. or selected country), debt share w_d and ERP are used in both currencies.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2018&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
FRED_US10Y_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30.0  # %

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# OECD jurisdiction labels -> Trading Economics country names
OECD_TO_TE = {
    "Korea": "South Korea",
    "Czechia": "Czech Republic",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "China (People's Republic of)": "China",
    "Slovak Republic": "Slovakia",
    "Hong Kong, China": "Hong Kong",
    "Hong Kong (China)": "Hong Kong",
    "Viet Nam": "Vietnam",
    "Russian Federation": "Russia",
    "Chinese Taipei": "Taiwan",
    "Côte d'Ivoire": "Ivory Coast",
    "Congo": "Republic of the Congo",
    "Democratic Republic of the Congo": "Congo",
    "Macau, China": "Macau",
    "Curaçao": "Curacao",
}

# Approximate OECD combined statutory CIT rates (Corporate Tax Statistics 2024),
# used ONLY if the OECD API cannot be reached. Shown as "fallback" in the app.
OECD_FALLBACK_2024 = {
    "United States": 25.6, "Argentina": 35.0, "Australia": 30.0, "Austria": 23.0,
    "Belgium": 25.0, "Brazil": 34.0, "Canada": 26.1, "Chile": 27.0, "China": 25.0,
    "Colombia": 35.0, "Czech Republic": 21.0, "Denmark": 22.0, "Finland": 20.0,
    "France": 25.8, "Germany": 29.9, "Greece": 22.0, "Hungary": 9.0, "India": 25.2,
    "Indonesia": 22.0, "Ireland": 12.5, "Israel": 23.0, "Italy": 27.8, "Japan": 29.7,
    "South Korea": 26.4, "Mexico": 30.0, "Netherlands": 25.8, "New Zealand": 28.0,
    "Norway": 22.0, "Peru": 29.5, "Poland": 19.0, "Portugal": 31.5, "South Africa": 27.0,
    "Spain": 25.0, "Sweden": 20.6, "Switzerland": 19.6, "Turkey": 25.0,
    "United Kingdom": 25.0,
}

EURO_AREA = {
    "Austria", "Belgium", "Croatia", "Cyprus", "Estonia", "Finland", "France", "Germany",
    "Greece", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
    "Netherlands", "Portugal", "Slovakia", "Slovenia", "Spain",
}
CURRENCY = {
    "United States": "USD", "Argentina": "ARS", "Australia": "AUD", "Brazil": "BRL",
    "Canada": "CAD", "Chile": "CLP", "China": "CNY", "Colombia": "COP",
    "Czech Republic": "CZK", "Denmark": "DKK", "Egypt": "EGP", "Hong Kong": "HKD",
    "Hungary": "HUF", "Iceland": "ISK", "India": "INR", "Indonesia": "IDR",
    "Israel": "ILS", "Japan": "JPY", "Kenya": "KES", "Malaysia": "MYR", "Mexico": "MXN",
    "New Zealand": "NZD", "Nigeria": "NGN", "Norway": "NOK", "Pakistan": "PKR",
    "Peru": "PEN", "Philippines": "PHP", "Poland": "PLN", "Romania": "RON",
    "Russia": "RUB", "Serbia": "RSD", "Singapore": "SGD", "South Africa": "ZAR",
    "South Korea": "KRW", "Sweden": "SEK", "Switzerland": "CHF", "Taiwan": "TWD",
    "Thailand": "THB", "Turkey": "TRY", "Ukraine": "UAH", "United Kingdom": "GBP",
    "Vietnam": "VND", "Bulgaria": "BGN", "Botswana": "BWP", "Morocco": "MAD",
    "Namibia": "NAD", "Sri Lanka": "LKR", "Bangladesh": "BDT", "Qatar": "QAR",
    "Uganda": "UGX", "Zambia": "ZMW", "Mauritius": "MUR", "Jordan": "JOD", "Kazakhstan": "KZT",
}


# Countries listed on tradingeconomics.com/bonds (slugs), used when the table itself is blocked
TE_SLUGS = [
    "united-states", "united-kingdom", "japan", "australia", "germany", "brazil", "russia", "india",
    "canada", "italy", "france", "south-africa", "china", "switzerland", "mexico", "netherlands",
    "new-zealand", "portugal", "south-korea", "spain", "greece", "turkey", "finland", "lithuania",
    "norway", "poland", "romania", "slovakia", "slovenia", "sweden", "hungary", "iceland", "ireland",
    "austria", "belgium", "croatia", "czech-republic", "denmark", "peru", "chile", "colombia",
    "singapore", "taiwan", "thailand", "vietnam", "hong-kong", "indonesia", "israel", "malaysia",
    "pakistan", "philippines", "zambia", "kenya", "nigeria",
]


def slug_to_country(slug: str) -> str:
    return slug.replace("-", " ").title()


def country_to_slug(country: str) -> str:
    return country.strip().lower().replace(" ", "-")


def currency_of(country: str) -> str:
    if country in EURO_AREA:
        return "EUR"
    return CURRENCY.get(country, "FC")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str, timeout: int = 25) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


# ----------------------------------------------------------------------------
# Calculation engine (pure functions - no Streamlit)
# ----------------------------------------------------------------------------
def lever_beta(beta_u: float, tax: float, debt_share: float) -> float:
    """Hamada: beta_L = beta_U * (1 + (1 - t) * D/E)."""
    if debt_share >= 1:
        raise ValueError("Debt share must be below 100%.")
    de = debt_share / (1 - debt_share)
    return beta_u * (1 + (1 - tax) * de)


def convert_rate(k_usd: float, y_us: float, y_fc: float, method: str = "exact") -> float:
    """Translate a USD rate into foreign-currency terms via the 10Y yield differential."""
    if method == "additive":
        return k_usd + (y_fc - y_us)
    return (1 + k_usd) * (1 + y_fc) / (1 + y_us) - 1


def compute(
    beta_u: float,
    tax: float,
    debt_share: float,
    erp: float,
    y_us: float,
    y_fc: float,
    spread_usd: float,
    spread_fc_override: float | None = None,
    method: str = "exact",
) -> dict:
    """Return all intermediate values for both currencies."""
    w_d = debt_share
    w_e = 1 - w_d
    beta_l = lever_beta(beta_u, tax, w_d)

    ke_usd = y_us + beta_l * erp
    kd_usd = y_us + spread_usd

    factor = (1 + y_fc) / (1 + y_us)
    ke_fc = convert_rate(ke_usd, y_us, y_fc, method)
    # Class method: s* = s x F, R_d* = R_f* + s*  (same as applying F to the gross pretax rate)
    spread_fc_auto = spread_usd * factor if method == "exact" else spread_usd
    spread_fc = spread_fc_auto if spread_fc_override is None else spread_fc_override
    kd_fc = y_fc + spread_fc

    kd_usd_at = kd_usd * (1 - tax)
    kd_fc_at = kd_fc * (1 - tax)
    wacc_usd = w_e * ke_usd + w_d * kd_usd_at
    wacc_fc = w_e * ke_fc + w_d * kd_fc_at

    return dict(
        factor=factor, beta_u=beta_u, beta_l=beta_l, tax=tax, w_d=w_d, w_e=w_e, de=w_d / w_e, erp=erp,
        y_us=y_us, y_fc=y_fc,
        ke_usd=ke_usd, ke_fc=ke_fc,
        spread_usd=spread_usd, spread_fc=spread_fc, spread_fc_auto=spread_fc_auto,
        kd_usd=kd_usd, kd_fc=kd_fc, kd_usd_at=kd_usd_at, kd_fc_at=kd_fc_at,
        wacc_usd=wacc_usd, wacc_fc=wacc_fc,
    )


# ----------------------------------------------------------------------------
# Parsers (separated from fetching so they can be tested offline)
# ----------------------------------------------------------------------------
def parse_betas_xls(content: bytes) -> tuple[pd.DataFrame, str]:
    """Return DataFrame[Industry, Firms, Unlevered beta] and the 'as of' date text."""
    xls = pd.ExcelFile(io.BytesIO(content))
    sheet = next((s for s in xls.sheet_names if "industry" in s.lower()), xls.sheet_names[0])
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)

    hdr_row = None
    for i in range(min(len(raw), 40)):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if "industry name" in vals:
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError("Could not find 'Industry Name' header in betas.xls")

    as_of = ""
    for i in range(hdr_row):
        row = [v for v in raw.iloc[i].tolist() if pd.notna(v)]
        txt = " ".join(str(v) for v in row)
        if "date" in txt.lower() or "updated" in txt.lower():
            as_of = txt
            break

    df = raw.iloc[hdr_row + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr_row].tolist()]
    cols = {c.lower(): c for c in df.columns}
    ub_col = cols.get("unlevered beta")
    if ub_col is None:
        raise ValueError("Column 'Unlevered beta' not found in betas.xls")
    firms_col = cols.get("number of firms")
    out = pd.DataFrame({
        "Industry": df[cols["industry name"]].map(lambda x: "" if pd.isna(x) else str(x).strip()),
        "Firms": pd.to_numeric(df[firms_col], errors="coerce") if firms_col else np.nan,
        "Unlevered beta": pd.to_numeric(df[ub_col], errors="coerce"),
    })
    out = out[out["Unlevered beta"].notna() & (out["Industry"] != "")]
    out = out[~out["Industry"].str.lower().str.startswith("total market")]
    return out.reset_index(drop=True), as_of


def parse_ratings_html(html: str) -> tuple[float, str, str]:
    """Return (BBB spread as decimal, rating label, 'as of' text) for large non-financial firms."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d\d)",
        text,
    )
    as_of = f"{m.group(1)} {m.group(2)}" if m else ""

    tables = pd.read_html(io.StringIO(html))
    for tbl in tables:  # first matching table is the large-firm table
        for _, row in tbl.iterrows():
            vals = ["" if pd.isna(v) else str(v).strip() for v in row.tolist()]
            for j, v in enumerate(vals):
                if v.replace(" ", "").lower() in ("baa2/bbb",):
                    for w in vals[j + 1:]:
                        mm = re.search(r"(-?\d+(?:\.\d+)?)\s*%", w)
                        if mm:
                            return float(mm.group(1)) / 100, v, as_of
    raise ValueError("Baa2/BBB row not found on ratings page")


def parse_te_bonds(html: str) -> pd.DataFrame:
    """Return DataFrame[Country, Yield, Date] from the tradingeconomics.com/bonds table.

    Rows look like: <tr data-symbol="USGG10YR:IND"> ... <a href="/united-states/government-bond-yield">
    <b>United States</b></a> ... <td id="p">5.1420</td> ... <td id="date">Nov/16</td>
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"^/[^/]+/government-bond-yield"))
        if not a:
            continue
        slug = re.search(r"^/([^/]+)/government-bond-yield", a["href"]).group(1)
        if slug in ("euro-area",):
            continue
        name = a.get_text(" ", strip=True) or slug.replace("-", " ").title()
        p_td = tr.find("td", id="p")
        if p_td is None:
            continue
        txt = p_td.get_text(strip=True).replace("%", "")
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", txt):
            continue
        d_td = tr.find("td", id="date")
        rows.append({"Country": slug_to_country(slug), "Yield": float(txt) / 100,
                     "Date": d_td.get_text(strip=True) if d_td else "", "TE label": name})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No yield rows in the Trading Economics bonds table (page blocked or not rendered)")
    return df.drop_duplicates("Country").reset_index(drop=True)


def parse_te_country_page(html: str) -> tuple[float, str]:
    """Return (10Y yield as decimal, date text) from a /<country>/government-bond-yield page.

    The exact value is in the embedded TEChartsMeta JSON ("last"); the date is in the meta description
    ("The yield on Colombia 10Y Bond Yield eased to 12.70% on November 13, 2026, ...").
    """
    value = None
    m = re.search(r"TEChartsMeta\s*=\s*(\[.*?\]);", html, re.S)
    if m:
        try:
            meta = json.loads(m.group(1))[0]
            value = float(meta.get("last", meta.get("value")))
        except Exception:  # noqa: BLE001
            value = None
    soup = BeautifulSoup(html, "lxml")
    desc_tag = soup.find("meta", attrs={"name": "description"})
    desc = desc_tag.get("content", "") if desc_tag else ""
    d = re.search(r"(?:to|at)\s+(-?\d+(?:\.\d+)?)%\s+on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", desc)
    if value is None and d:
        value = float(d.group(1))
    if value is None:
        raise ValueError("Yield not found on country page")
    return value / 100, (d.group(2) if d else "")


def parse_oecd_csv(text: str) -> tuple[pd.DataFrame, str]:
    """Return DataFrame[Country, Tax rate, Year] of combined statutory CIT rates (latest year)."""
    df = pd.read_csv(io.StringIO(text))
    label_cols = [c for c in df.columns if df[c].dtype == object]
    # keep rows whose measure label mentions 'combined'
    meas_cols = [c for c in df.columns if c.lower() == "measure"]
    meas_lbl = [c for c in label_cols if "measure" in c.lower() and c not in ("MEASURE",)]
    mask = pd.Series(False, index=df.index)
    for c in meas_lbl + meas_cols:
        mask |= df[c].fillna("").astype(str).str.lower().str.contains("combined", regex=False)
    if mask.any():
        df = df[mask]
    # choose the dominant (non-targeted) category for any remaining dimensions
    for dim in ("TARGETING", "SECTOR", "UNIT_MEASURE"):
        if dim in df.columns and df[dim].nunique() > 1:
            pref = [v for v in df[dim].unique() if str(v) in ("_Z", "_T", "NT", "_N")]
            keep = pref[0] if pref else df[dim].value_counts().idxmax()
            df = df[df[dim] == keep]
    area_lbl = next((c for c in df.columns if c.lower() in ("reference area", "ref_area_label")), "REF_AREA")
    df = df.dropna(subset=["OBS_VALUE"])
    df["TIME_PERIOD"] = pd.to_numeric(df["TIME_PERIOD"], errors="coerce")
    df = df.sort_values("TIME_PERIOD").groupby(area_lbl, as_index=False).last()
    out = pd.DataFrame({
        "Country": df[area_lbl].astype(str).map(lambda x: OECD_TO_TE.get(x, x)),
        "Tax rate": df["OBS_VALUE"].astype(float) / 100,
        "Year": df["TIME_PERIOD"].astype(int),
    })
    latest = int(out["Year"].max()) if not out.empty else ""
    return out.reset_index(drop=True), str(latest)


# ----------------------------------------------------------------------------
# Fetchers (cached; refresh button clears cache)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_betas():
    try:
        df, as_of = parse_betas_xls(_get(BETAS_URL).content)
        return dict(df=df, as_of=as_of or "see file header", fetched=now_utc(), status="live", error="")
    except Exception as e:  # noqa: BLE001
        return dict(df=pd.DataFrame(columns=["Industry", "Firms", "Unlevered beta"]),
                    as_of="", fetched=now_utc(), status="failed", error=str(e))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_bbb_spread():
    try:
        spread, label, as_of = parse_ratings_html(_get(RATINGS_URL).text)
        return dict(value=spread, label=label, as_of=as_of, fetched=now_utc(), status="live", error="")
    except Exception as e:  # noqa: BLE001
        return dict(value=0.0111, label="Baa2/BBB", as_of="January 2026 (fallback)",
                    fetched=now_utc(), status="fallback", error=str(e))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_tax_rates():
    try:
        df, year = parse_oecd_csv(_get(OECD_API_URL, timeout=40).text)
        if df.empty:
            raise ValueError("OECD API returned no rows")
        return dict(df=df, as_of=year, fetched=now_utc(), status="live", error="")
    except Exception as e:  # noqa: BLE001
        df = pd.DataFrame({"Country": list(OECD_FALLBACK_2024),
                           "Tax rate": [v / 100 for v in OECD_FALLBACK_2024.values()],
                           "Year": 2024})
        return dict(df=df, as_of="2024 (fallback snapshot)", fetched=now_utc(),
                    status="fallback", error=str(e))


@st.cache_data(ttl=1 * 3600, show_spinner=False)
def fetch_yields():
    """Try the full /bonds table first. Trading Economics loads that table from
    /bonds?partial=markets, which sits behind an AWS WAF bot challenge (HTTP 202, empty body)
    for non-browser clients, so the app falls back to per-country pages on demand."""
    try:
        with requests.Session() as sess:
            sess.headers.update(HEADERS)
            sess.get(TE_BONDS_URL, timeout=25)  # pick up cookies
            last_err = None
            for url in (TE_BONDS_URL + "?partial=markets", TE_BONDS_URL):
                try:
                    r = sess.get(url, timeout=25, headers={"Referer": TE_BONDS_URL})
                    if r.status_code != 200 or not r.text.strip():
                        raise ValueError(f"HTTP {r.status_code} (bot challenge)" if r.status_code == 202
                                         else f"HTTP {r.status_code}")
                    df = parse_te_bonds(r.text)
                    df["Source"] = "Trading Economics (bonds table)"
                    return dict(df=df, fetched=now_utc(), status="live", mode="table", error="")
                except Exception as e:  # noqa: BLE001
                    last_err = e
            raise last_err
    except Exception as e:  # noqa: BLE001
        return dict(df=pd.DataFrame(columns=["Country", "Yield", "Date", "TE label", "Source"]),
                    fetched=now_utc(), status="live", mode="per-country",
                    error=f"Bonds table unavailable ({e}); using per-country pages.")


@st.cache_data(ttl=1 * 3600, show_spinner=False)
def fetch_country_yield(country: str):
    """10Y yield for one country from tradingeconomics.com/<slug>/government-bond-yield.
    Falls back to FRED (DGS10) for the United States."""
    slug = country_to_slug(country)
    url = f"https://tradingeconomics.com/{slug}/government-bond-yield"
    try:
        y, date = parse_te_country_page(_get(url).text)
        return dict(value=y, date=date, source="Trading Economics (country page)", url=url,
                    fetched=now_utc(), status="live", error="")
    except Exception as e:  # noqa: BLE001
        err = f"{url}: {e}"
    if country == "United States":
        try:
            fred = pd.read_csv(io.StringIO(_get(FRED_US10Y_URL).text))
            fred.columns = ["Date", "Yield"]
            fred["Yield"] = pd.to_numeric(fred["Yield"], errors="coerce")
            last = fred.dropna().iloc[-1]
            return dict(value=last["Yield"] / 100, date=str(last["Date"]), source="FRED DGS10 (fallback)",
                        url=FRED_US10Y_URL, fetched=now_utc(), status="fallback", error=err)
        except Exception as e2:  # noqa: BLE001
            err += f"; FRED: {e2}"
    return dict(value=np.nan, date="", source="", url=url, fetched=now_utc(), status="failed", error=err)


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def pct(x: float, d: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def data_input(label, key, fetched, source="", is_pct=True, min_v=-50.0, max_v=100.0, step=0.01):
    """Number field showing the fetched value as a grey placeholder. Blank = fetched value;
    a typed number overrides it. `source` is shown in the (?) tooltip. Returns (value_used, is_manual)."""
    have = fetched is not None and not (isinstance(fetched, float) and np.isnan(fetched))
    shown = (fetched * 100 if is_pct else fetched) if have else None
    v = st.number_input(f"{label} (%)" if is_pct else label, value=None,
                        placeholder=f"{shown:.4f}" if have else "not available — type a value",
                        key=key, min_value=min_v, max_value=max_v, step=step, format="%.4f",
                        help=source or None)
    if v is None:
        return (fetched if have else np.nan), False
    st.caption(f"✏️ Manual (fetched: {shown:.4f})" if have else "✏️ Manual")
    return (v / 100 if is_pct else v), True


def status_badge(status: str) -> str:
    return {"live": "🟢 live", "fallback": "🟠 fallback", "failed": "🔴 failed", "manual": "✏️ manual"}.get(status, status)


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")
    st.title("🌍 International Cost of Capital")
    st.caption("Cost of equity, cost of debt and WACC for a project — in USD and in the selected country's currency.")

    # ---------------- Data ----------------
    with st.spinner("Fetching latest data…"):
        betas, bbb, taxes, ylds = fetch_betas(), fetch_bbb_spread(), fetch_tax_rates(), fetch_yields()

    beta_df = betas["df"]
    tax_df = taxes["df"]
    y_df = ylds["df"]
    tax_map = dict(zip(tax_df["Country"], tax_df["Tax rate"]))
    y_map = dict(zip(y_df["Country"], y_df["Yield"]))

    # ---------------- Sidebar ----------------
    sb = st.sidebar
    if sb.button("🔄 Refresh data", width="stretch", help="Clear the cache and re-fetch all sources"):
        st.cache_data.clear()
        st.rerun()

    sb.header("Project inputs")
    industries = beta_df["Industry"].tolist() or ["(manual beta)"]
    ind_idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
    industry = sb.selectbox("Industry (Damodaran, U.S.)", industries, index=ind_idx)

    countries = sorted(set(y_map) | {slug_to_country(x) for x in TE_SLUGS} | set(tax_map) | {DEFAULT_COUNTRY})
    country = sb.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
    ccy = currency_of(country)
    if ccy == "USD":  # keep column labels distinct when the target country is the U.S.
        ccy = "USD, target-country basis"

    erp = sb.number_input("Equity risk premium (%)", min_value=0.0, max_value=20.0, value=DEFAULT_ERP,
                          step=0.1, format="%.2f") / 100
    w_d = sb.number_input("Target debt share D/(D+E) (%)", min_value=0.0, max_value=95.0, value=DEFAULT_DEBT,
                          step=1.0, format="%.2f") / 100

    tax_choice = sb.radio("Project tax rate", ["U.S. rate", f"{country} rate"], index=0, horizontal=True,
                          help="The same tax rate is applied in both currencies (beta levering and debt tax shield).")
    method_key = "exact"  # class method: (1 + R*) = (1 + R)(1 + R_f*)/(1 + R_f)

    sb.header("Data inputs")
    sb.caption("Grey values are fetched. Type over any of them to override; clear the field to go back.")
    tax_note = f"OECD Corporate Tax Statistics — combined statutory CIT rate, {taxes['as_of']} (OECD Data API)."
    is_us = country == "United States"
    auto_beta = float(beta_df.loc[beta_df["Industry"] == industry, "Unlevered beta"].iloc[0]) \
        if industry in set(beta_df["Industry"]) else np.nan
    n_firms = beta_df.loc[beta_df["Industry"] == industry, "Firms"]
    n_firms = f", {int(n_firms.iloc[0])} firms" if len(n_firms) and pd.notna(n_firms.iloc[0]) else ""
    with sb:
        beta_u, ov_beta = data_input("Unlevered beta", f"beta|{industry}", auto_beta, is_pct=False,
                                     min_v=0.0, max_v=5.0,
                                     source=f"Damodaran betas.xls, column 'Unlevered beta' — {industry}{n_firms}. "
                                            f"{betas['as_of']}.")
    if np.isnan(beta_u):
        sb.error("Beta data unavailable — type an unlevered beta.")
        st.stop()

    def yield_info(c):
        if c in y_map:
            row = y_df.loc[y_df["Country"] == c].iloc[0]
            return dict(value=float(row["Yield"]), date=str(row["Date"]), source=str(row["Source"]),
                        url=TE_BONDS_URL, status="live", error="")
        return fetch_country_yield(c)

    yinfo = {"United States": yield_info("United States")}
    if not is_us:
        yinfo[country] = yield_info(country)

    with sb.expander("Corporate tax rates (OECD)", expanded=False):
        t_us, ov_tus = data_input("U.S. tax rate", "tax|United States", tax_map.get("United States", np.nan),
                                  min_v=0.0, source=tax_note)
        if is_us:
            t_fc, ov_tfc = t_us, False
        else:
            t_fc, ov_tfc = data_input(f"{country} tax rate", f"tax|{country}", tax_map.get(country, np.nan),
                                      min_v=0.0, source=tax_note)

    with sb.expander("10Y government yields (Trading Economics)", expanded=False):
        def ysrc(info):
            if info["status"] == "failed":
                return "Could not fetch — type a value. " + info.get("error", "")
            return f"{info['value']:.4%} · {info['date']} · {info['source']}"

        d_us = yinfo["United States"]
        y_us, ov_yus = data_input("U.S. 10Y yield", "yield|United States", d_us["value"], source=ysrc(d_us))
        if is_us:
            y_fc, ov_yfc = y_us, False
            st.caption("Selected country is the U.S.: its yield equals the U.S. yield.")
        else:
            d_fc = yinfo[country]
            y_fc, ov_yfc = data_input(f"{country} 10Y yield", f"yield|{country}", d_fc["value"], source=ysrc(d_fc))

    with sb.expander("BBB borrowing spreads", expanded=False):
        s_usd, ov_susd = data_input("USD BBB spread", "spread|USD", bbb["value"], min_v=0.0,
                                    source=f"Damodaran ratings.html — {bbb['label']} spread, large non-financial "
                                           f"service firms, {bbb['as_of']}.")
        have_y = not (np.isnan(y_us) or np.isnan(y_fc))
        s_fc_auto = s_usd * (1 + y_fc) / (1 + y_us) if have_y else np.nan
        fac = (1 + y_fc) / (1 + y_us) if have_y else np.nan
        s_fc_val, ov_sfc = data_input(f"{ccy} BBB spread", f"spread|{country}", s_fc_auto, min_v=0.0,
                                      source=f"Auto: s* = s × F, with F = (1 + y_FC)/(1 + y_US) = {fac:.6f}. "
                                             "Type a value to override; clear it to switch back to auto.")
        s_fc_in = s_fc_val if ov_sfc else None

    missing = [n for n, v in [("U.S. 10Y yield", y_us), (f"{country} 10Y yield", y_fc),
                              ("U.S. tax rate", t_us), (f"{country} tax rate", t_fc)]
               if v is None or np.isnan(v)]
    tax = t_us if tax_choice.startswith("U.S.") else t_fc
    needed = [m for m in missing if ("tax" not in m) or (m.startswith("U.S.") == tax_choice.startswith("U.S."))]
    if needed:
        st.error("Missing data: " + ", ".join(needed) + ". Use the manual overrides in the sidebar.")
        st.stop()

    r = compute(beta_u, tax, w_d, erp, y_us, y_fc, s_usd,
                s_fc_in if ov_sfc else None, method_key)

    # ---------------- Headline ----------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cost of equity (USD)", pct(r["ke_usd"]))
    c2.metric(f"Cost of equity ({ccy})", pct(r["ke_fc"]), f"{(r['ke_fc'] - r['ke_usd']) * 1e4:+.0f} bp vs USD")
    c3.metric("WACC (USD)", pct(r["wacc_usd"]))
    c4.metric(f"WACC ({ccy})", pct(r["wacc_fc"]), f"{(r['wacc_fc'] - r['wacc_usd']) * 1e4:+.0f} bp vs USD")

    tab_res, tab_all, tab_src, tab_about = st.tabs(
        ["📊 Results", "🏭 All industries", "📚 Sources & data", "ℹ️ Method & assumptions"])

    # ---------------- Results ----------------
    with tab_res:
        st.subheader(f"{industry} — project in {country}")
        tbl = pd.DataFrame(
            [
                ["10Y government yield (risk-free)", pct(r["y_us"]), pct(r["y_fc"])],
                ["Unlevered beta", f"{r['beta_u']:.4f}", f"{r['beta_u']:.4f}"],
                ["Project tax rate", pct(r["tax"]), pct(r["tax"])],
                ["Debt / Equity", f"{r['de']:.4f}", f"{r['de']:.4f}"],
                ["Levered beta", f"{r['beta_l']:.4f}", f"{r['beta_l']:.4f}"],
                ["Equity risk premium", pct(r["erp"]), "(implied by conversion)"],
                ["Cost of equity", pct(r["ke_usd"]), pct(r["ke_fc"])],
                ["BBB spread", pct(r["spread_usd"]),
                 pct(r["spread_fc"]) + (" (manual)" if ov_sfc else " (auto)")],
                ["Pre-tax cost of debt", pct(r["kd_usd"]), pct(r["kd_fc"])],
                ["After-tax cost of debt", pct(r["kd_usd_at"]), pct(r["kd_fc_at"])],
                ["Equity weight", pct(r["w_e"], 1), pct(r["w_e"], 1)],
                ["Debt weight", pct(r["w_d"], 1), pct(r["w_d"], 1)],
                ["WACC", pct(r["wacc_usd"]), pct(r["wacc_fc"])],
            ],
            columns=["Item", "USD", ccy],
        )
        st.table(tbl.set_index("Item"))
        if ov_sfc:
            st.caption(f"Auto {ccy} spread would have been {pct(r['spread_fc_auto'])}.")
        with st.expander("Calculation steps"):
            st.markdown(f"""
| Step | Formula | Value |
|---|---|---|
| D/E | {pct(w_d,1)} / {pct(1-w_d,1)} | {r['de']:.4f} |
| Levered beta | {r['beta_u']:.4f} × [1 + (1 − {pct(tax)}) × {r['de']:.4f}] | {r['beta_l']:.4f} |
| k_e (USD) | {pct(y_us)} + {r['beta_l']:.4f} × {pct(erp)} | {pct(r['ke_usd'],4)} |
| k_d (USD) | {pct(y_us)} + {pct(r['spread_usd'])} | {pct(r['kd_usd'],4)} |
| F | (1 + {pct(y_fc,3)}) / (1 + {pct(y_us,3)}) | {r['factor']:.7f} |
| k_e ({ccy}) | (1 + {pct(r['ke_usd'],4)}) × {r['factor']:.7f} − 1 | {pct(r['ke_fc'],4)} |
| s* ({ccy}) | {"manual override" if ov_sfc else f"{pct(r['spread_usd'],4)} × {r['factor']:.7f}"} | {pct(r['spread_fc'],4)} |
| k_d ({ccy}) | {pct(y_fc,3)} + {pct(r['spread_fc'],4)} | {pct(r['kd_fc'],4)} |
| WACC (USD) | {pct(1-w_d,1)} × {pct(r['ke_usd'],4)} + {pct(w_d,1)} × {pct(r['kd_usd'],4)} × (1 − {pct(tax)}) | {pct(r['wacc_usd'],4)} |
| WACC ({ccy}) | {pct(1-w_d,1)} × {pct(r['ke_fc'],4)} + {pct(w_d,1)} × {pct(r['kd_fc'],4)} × (1 − {pct(tax)}) | {pct(r['wacc_fc'],4)} |
""")


        flags = [n for n, f in [("beta", ov_beta), ("U.S. tax", ov_tus), (f"{country} tax", ov_tfc),
                                ("USD spread", ov_susd), (f"{ccy} spread", ov_sfc),
                                ("U.S. yield", ov_yus), (f"{country} yield", ov_yfc)] if f]
        if flags:
            st.info("Manual overrides active: " + ", ".join(flags))

        chart_df = pd.DataFrame({
            "Measure": ["Cost of equity", "Pre-tax cost of debt", "WACC"] * 2,
            "Currency": ["USD"] * 3 + [ccy] * 3,
            "Rate": [r["ke_usd"], r["kd_usd"], r["wacc_usd"], r["ke_fc"], r["kd_fc"], r["wacc_fc"]],
        })
        st.altair_chart(
            alt.Chart(chart_df).mark_bar().encode(
                x=alt.X("Currency:N", title=None),
                y=alt.Y("Rate:Q", axis=alt.Axis(format="%"), title=None),
                color=alt.Color("Currency:N", legend=None),
                column=alt.Column("Measure:N", title=None),
                tooltip=["Measure", "Currency", alt.Tooltip("Rate:Q", format=".2%")],
            ).properties(width=160, height=260),
        )

        inputs = {
            "Industry": industry, "Country": country, "Currency": ccy,
            "Tax rate used": tax_choice,
            "Generated": now_utc(),
        }
        export = pd.DataFrame(
            [{"Item": k, "USD": v, ccy: v} for k, v in inputs.items()] +
            [{"Item": k, "USD": r[a], ccy: r[b]} for k, a, b in [
                ("Gov 10Y yield", "y_us", "y_fc"), ("Unlevered beta", "beta_u", "beta_u"),
                ("Tax rate", "tax", "tax"), ("D/E", "de", "de"), ("Levered beta", "beta_l", "beta_l"),
                ("ERP", "erp", "erp"), ("Cost of equity", "ke_usd", "ke_fc"),
                ("BBB spread", "spread_usd", "spread_fc"), ("Pre-tax cost of debt", "kd_usd", "kd_fc"),
                ("After-tax cost of debt", "kd_usd_at", "kd_fc_at"), ("Debt weight", "w_d", "w_d"),
                ("WACC", "wacc_usd", "wacc_fc")]]
        )
        st.download_button("⬇️ Download results (CSV)", export.to_csv(index=False).encode(),
                           f"cost_of_capital_{country.replace(' ', '_')}.csv", "text/csv")

    # ---------------- All industries ----------------
    with tab_all:
        st.subheader(f"All industries — {country}, same tax / debt share / ERP / yields")
        if beta_df.empty:
            st.warning("Industry beta data unavailable.")
            all_df = pd.DataFrame()
        else:
            rows = []
            for _, b in beta_df.iterrows():
                x = compute(b["Unlevered beta"], tax, w_d, erp, y_us, y_fc, s_usd,
                            s_fc_in if ov_sfc else None, method_key)
                rows.append({"Industry": b["Industry"], "Firms": b["Firms"],
                             "Unlevered beta": x["beta_u"], "Levered beta": x["beta_l"],
                             "Cost of equity (USD)": x["ke_usd"], f"Cost of equity ({ccy})": x["ke_fc"],
                             "WACC (USD)": x["wacc_usd"], f"WACC ({ccy})": x["wacc_fc"]})
            all_df = pd.DataFrame(rows).sort_values("WACC (USD)", ascending=False).reset_index(drop=True)
            fmt = {c: "{:.2%}" for c in all_df.columns if "Cost" in c or "WACC" in c}
            fmt.update({"Unlevered beta": "{:.3f}", "Levered beta": "{:.3f}", "Firms": "{:.0f}"})
            st.dataframe(
                all_df.style.format(fmt).apply(
                    lambda s: ["background-color: rgba(255,200,0,.25)" if v == industry else "" for v in s],
                    subset=["Industry"]),
                width="stretch", height=420)
            long = all_df.melt(id_vars="Industry", value_vars=["WACC (USD)", f"WACC ({ccy})"],
                               var_name="Series", value_name="WACC")
            st.altair_chart(
                alt.Chart(long).mark_circle(size=45).encode(
                    y=alt.Y("Industry:N", sort=list(all_df["Industry"]), title=None,
                            axis=alt.Axis(labelFontSize=9)),
                    x=alt.X("WACC:Q", axis=alt.Axis(format="%")),
                    color="Series:N",
                    tooltip=["Industry", "Series", alt.Tooltip("WACC:Q", format=".2%")],
                ).properties(height=max(400, 11 * len(all_df))),
                width="stretch")
            st.download_button("⬇️ Download all-industry table (CSV)", all_df.to_csv(index=False).encode(),
                               f"all_industries_{country.replace(' ', '_')}.csv", "text/csv")

        # Excel workbook with everything
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            export.to_excel(xw, sheet_name="Selected project", index=False)
            if not all_df.empty:
                all_df.to_excel(xw, sheet_name="All industries", index=False)
            pd.DataFrame(source_rows(betas, bbb, taxes, ylds, yinfo)).to_excel(xw, sheet_name="Sources", index=False)
        st.download_button("⬇️ Download full workbook (Excel)", buf.getvalue(),
                           "international_cost_of_capital.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---------------- Sources ----------------
    with tab_src:
        st.subheader("Data sources, dates and status")
        st.table(pd.DataFrame(source_rows(betas, bbb, taxes, ylds, yinfo)).set_index("Data"))
        for name, d in [("Betas", betas), ("BBB spread", bbb), ("Tax rates", taxes)]:
            if d.get("error"):
                st.warning(f"{name}: {d['error']}")
        if ylds.get("mode") == "per-country":
            st.caption("Yields are read from each country's Trading Economics page "
                       "(tradingeconomics.com/<country>/government-bond-yield). The /bonds overview table is "
                       "loaded by a bot-protected script, so the app uses the country pages, which show the same quote.")
        for c, info in yinfo.items():
            if info.get("error"):
                st.warning(f"{c} yield: {info['error']}")
        if not y_df.empty:
            with st.expander("10Y government yields (Trading Economics bonds table)"):
                st.dataframe(y_df.assign(Yield=y_df["Yield"].map(lambda v: f"{v:.4%}")), width="stretch")
        with st.expander("Corporate tax rates (OECD)"):
            st.dataframe(tax_df.assign(**{"Tax rate": tax_df["Tax rate"].map(lambda v: f"{v:.2%}")}),
                         width="stretch")
        with st.expander("Industry unlevered betas (Damodaran)"):
            st.dataframe(beta_df, width="stretch")

    # ---------------- About ----------------
    with tab_about:
        st.markdown(ABOUT_MD)


def source_rows(betas, bbb, taxes, ylds, yinfo):
    rows = [
        {"Data": f"10Y government yield — {c}", "Source": info.get("source") or "Trading Economics",
         "URL": info.get("url", TE_BONDS_URL), "Data as of": info.get("date", ""),
         "Fetched": info.get("fetched", ylds["fetched"]), "Status": status_badge(info["status"])}
        for c, info in yinfo.items()
    ]
    return [
        {"Data": "Industry unlevered betas", "Source": "Aswath Damodaran (NYU Stern) — betas.xls",
         "URL": BETAS_URL, "Data as of": betas["as_of"], "Fetched": betas["fetched"],
         "Status": status_badge(betas["status"])},
        {"Data": "USD BBB spread (Baa2/BBB, large non-financial firms)",
         "Source": "Aswath Damodaran — ratings.html", "URL": RATINGS_URL,
         "Data as of": bbb["as_of"], "Fetched": bbb["fetched"], "Status": status_badge(bbb["status"])},
        {"Data": "Corporate tax rates (combined statutory CIT)",
         "Source": "OECD Corporate Tax Statistics (via OECD Data API)", "URL": OECD_PAGE_URL,
         "Data as of": taxes["as_of"], "Fetched": taxes["fetched"], "Status": status_badge(taxes["status"])},
    ] + rows


ABOUT_MD = r"""
### Method (class notation; * denotes the foreign currency)

1. **Relever the beta.** $D/E = d/(1-d)$ and $\beta_E = \beta_U\,[1 + (1-\tau)\,D/E]$.
   $\beta_U$ is Damodaran's U.S. industry unlevered beta.
2. **Cost of equity in USD (CAPM).** $R_e = R_f + \beta_E \times ERP$, with the U.S. 10-year yield as $R_f$.
3. **Cost of debt in USD.** $R_d = R_f + s$, where $s$ is Damodaran's Baa2/BBB spread for large
   non-financial firms.
4. **Conversion factor.** $F = (1 + R_f^*)/(1 + R_f)$, using the selected country's 10-year yield as $R_f^*$
   (Fisher equation + relative PPP).
5. **Cost of equity in foreign currency.** $1 + R_e^* = (1 + R_e) \times F$.
6. **Cost of debt in foreign currency.** $s^* = s \times F$ and $R_d^* = R_f^* + s^*$.
   A manual foreign spread replaces $s^*$ and changes only the foreign-currency results.
7. **WACC** in each currency: $WACC = (1-d)\,R_e + d\,R_d\,(1-\tau)$.

### Key assumptions
- The same tax rate $\tau$ (U.S. or selected country, chosen in the sidebar), debt share $d$ and ERP are
  used in both currencies; interest is deducted in each financing currency.
- Project risk is the U.S. industry's business risk. No separate country equity risk premium is added;
  country risk enters only through the local government yield.
- Debt and the interest tax shield are assumed to have zero beta (basis for the relevering formula).
- The 10-year government yield differential proxies the expected inflation / depreciation differential.
  Local yields can also include sovereign default and term premia.
- Tax rate = OECD combined (central + sub-central) statutory corporate income tax rate.
- Data are cached (yields 1 hour, other sources 6 hours). Use **Refresh data** to re-fetch.
"""


if __name__ == "__main__":
    main()
