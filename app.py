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
        "Industry": df[cols["industry name"]].astype(str).str.strip(),
        "Firms": pd.to_numeric(df[firms_col], errors="coerce") if firms_col else np.nan,
        "Unlevered beta": pd.to_numeric(df[ub_col], errors="coerce"),
    })
    out = out[out["Unlevered beta"].notna() & (out["Industry"] != "nan")]
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
        flat = tbl.astype(str)
        for _, row in flat.iterrows():
            vals = [v.strip() for v in row.tolist()]
            for j, v in enumerate(vals):
                if v.replace(" ", "").lower() in ("baa2/bbb",):
                    for w in vals[j + 1:]:
                        mm = re.search(r"(-?\d+(?:\.\d+)?)\s*%", w)
                        if mm:
                            return float(mm.group(1)) / 100, v, as_of
    raise ValueError("Baa2/BBB row not found on ratings page")


def parse_te_bonds(html: str) -> pd.DataFrame:
    """Return DataFrame[Country, Yield, Date] of 10Y yields from tradingeconomics.com/bonds."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"/[^/]+/government-bond-yield"))
        if not a:
            continue
        label = a.get_text(" ", strip=True)
        if "10Y" not in label.upper():
            continue
        slug = re.search(r"/([^/]+)/government-bond-yield", a["href"]).group(1)
        country = slug.replace("-", " ").title()
        country = {"United States": "United States", "Euro Area": "Euro Area"}.get(country, country)
        tds = tr.find_all("td")
        nums = []
        for td in tds:
            t = td.get_text(strip=True).replace(",", "").replace("%", "")
            if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
                nums.append(float(t))
        if not nums:
            continue
        date_txt = tds[-1].get_text(strip=True) if tds else ""
        rows.append({"Country": country, "Yield": nums[0] / 100, "Date": date_txt, "TE label": label})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No 10Y rows parsed from Trading Economics")
    return df.drop_duplicates("Country").reset_index(drop=True)


def parse_oecd_csv(text: str) -> tuple[pd.DataFrame, str]:
    """Return DataFrame[Country, Tax rate, Year] of combined statutory CIT rates (latest year)."""
    df = pd.read_csv(io.StringIO(text))
    label_cols = [c for c in df.columns if df[c].dtype == object]
    # keep rows whose measure label mentions 'combined'
    meas_cols = [c for c in df.columns if c.lower() == "measure"]
    meas_lbl = [c for c in label_cols if "measure" in c.lower() and c not in ("MEASURE",)]
    mask = pd.Series(False, index=df.index)
    for c in meas_lbl + meas_cols:
        mask |= df[c].astype(str).str.lower().str.contains("combined")
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
    errors = []
    try:
        df = parse_te_bonds(_get(TE_BONDS_URL).text)
        df["Source"] = "Trading Economics"
        return dict(df=df, fetched=now_utc(), status="live", error="")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Trading Economics: {e}")
    # Fallback: U.S. 10Y from FRED so the default (US vs US) case still works
    try:
        fred = pd.read_csv(io.StringIO(_get(FRED_US10Y_URL).text))
        fred.columns = ["Date", "Yield"]
        fred["Yield"] = pd.to_numeric(fred["Yield"], errors="coerce")
        last = fred.dropna().iloc[-1]
        df = pd.DataFrame([{"Country": "United States", "Yield": last["Yield"] / 100,
                            "Date": str(last["Date"]), "TE label": "DGS10", "Source": "FRED (fallback)"}])
        return dict(df=df, fetched=now_utc(), status="fallback", error="; ".join(errors))
    except Exception as e:  # noqa: BLE001
        errors.append(f"FRED: {e}")
    return dict(df=pd.DataFrame(columns=["Country", "Yield", "Date", "TE label", "Source"]),
                fetched=now_utc(), status="failed", error="; ".join(errors))


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def pct(x: float, d: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def override_number(label, key, auto_value, help_txt="", fmt_pct=True, min_v=-50.0, max_v=100.0, step=0.01):
    """Checkbox + number input. Returns (value_used, is_manual)."""
    on = st.checkbox(f"Override {label}", key=f"ov_{key}", help=help_txt)
    if fmt_pct:
        default = 0.0 if auto_value is None or np.isnan(auto_value) else round(auto_value * 100, 4)
        v = st.number_input(f"{label} (%)", value=default, step=step, min_value=min_v, max_value=max_v,
                            key=f"in_{key}", disabled=not on, format="%.4f")
        return (v / 100 if on else auto_value), on
    default = 1.0 if auto_value is None or np.isnan(auto_value) else round(float(auto_value), 4)
    v = st.number_input(label, value=default, step=step, min_value=min_v, max_value=max_v,
                        key=f"in_{key}", disabled=not on, format="%.4f")
    return (v if on else auto_value), on


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

    countries = sorted(set(y_map) | set(tax_map) | {DEFAULT_COUNTRY})
    country = sb.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
    ccy = currency_of(country)
    if ccy == "USD":  # keep column labels distinct when the target country is the U.S.
        ccy = "USD, target-country basis"

    erp = sb.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1) / 100
    w_d = sb.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT, 1.0) / 100

    tax_choice = sb.radio("Project tax rate", ["U.S. rate", f"{country} rate"], index=0, horizontal=True,
                          help="The same tax rate is applied in both currencies (beta levering and debt tax shield).")
    method = sb.radio("Currency conversion", ["Exact (multiplicative)", "Additive approximation"], index=0,
                      help="Exact: (1+k$)(1+y_FC)/(1+y_US)−1.  Additive: k$ + (y_FC − y_US).")
    method_key = "exact" if method.startswith("Exact") else "additive"

    sb.header("Manual overrides")
    auto_beta = float(beta_df.loc[beta_df["Industry"] == industry, "Unlevered beta"].iloc[0]) \
        if industry in set(beta_df["Industry"]) else np.nan
    beta_u, ov_beta = override_number("Unlevered beta", "beta", auto_beta, fmt_pct=False, min_v=0.0, max_v=5.0)
    if np.isnan(beta_u):
        sb.error("Beta data unavailable — turn on the beta override.")
        st.stop()

    with sb.expander("Tax rates", expanded=False):
        t_us, ov_tus = override_number("U.S. tax rate", "tus", tax_map.get("United States", np.nan), min_v=0.0)
        t_fc, ov_tfc = override_number(f"{country} tax rate", "tfc", tax_map.get(country, np.nan), min_v=0.0)
    with sb.expander("Spreads", expanded=False):
        s_usd, ov_susd = override_number("USD BBB spread", "susd", bbb["value"], min_v=0.0)
        ov_sfc = st.checkbox(f"Override {ccy} BBB spread", key="ov_sfc",
                             help="If off, the foreign spread is computed automatically from the USD cost of debt.")
        s_fc_in = st.number_input(f"{ccy} BBB spread (%)", value=1.11, step=0.01, key="in_sfc",
                                  disabled=not ov_sfc, format="%.4f") / 100
    with sb.expander("Government 10Y yields", expanded=False):
        y_us, ov_yus = override_number("U.S. 10Y yield", "yus", y_map.get("United States", np.nan))
        y_fc, ov_yfc = override_number(f"{country} 10Y yield", "yfc",
                                       y_us if country == "United States" else y_map.get(country, np.nan))

    if country == "United States" and not ov_yfc:
        y_fc = y_us

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

    tab_res, tab_all, tab_check, tab_src, tab_about = st.tabs(
        ["📊 Results", "🏭 All industries", "✅ Checks", "📚 Sources & data", "ℹ️ Method & assumptions"])

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
                ["**Cost of equity**", pct(r["ke_usd"]), pct(r["ke_fc"])],
                ["BBB spread", pct(r["spread_usd"]),
                 pct(r["spread_fc"]) + (" (manual)" if ov_sfc else " (auto)")],
                ["Pre-tax cost of debt", pct(r["kd_usd"]), pct(r["kd_fc"])],
                ["After-tax cost of debt", pct(r["kd_usd_at"]), pct(r["kd_fc_at"])],
                ["Equity weight", pct(r["w_e"], 1), pct(r["w_e"], 1)],
                ["Debt weight", pct(r["w_d"], 1), pct(r["w_d"], 1)],
                ["**WACC**", pct(r["wacc_usd"]), pct(r["wacc_fc"])],
            ],
            columns=["Item", "USD", ccy],
        )
        st.table(tbl.set_index("Item"))
        if ov_sfc:
            st.caption(f"Auto {ccy} spread would have been {pct(r['spread_fc_auto'])}.")

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
            "Tax rate used": tax_choice, "Conversion": method,
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
            pd.DataFrame(source_rows(betas, bbb, taxes, ylds)).to_excel(xw, sheet_name="Sources", index=False)
        st.download_button("⬇️ Download full workbook (Excel)", buf.getvalue(),
                           "international_cost_of_capital.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---------------- Checks ----------------
    with tab_check:
        st.subheader("Built-in verification (uses current inputs)")
        base = dict(beta_u=beta_u, tax=tax, debt_share=w_d, erp=erp, y_us=y_us, spread_usd=s_usd, method=method_key)
        a = compute(**base, y_fc=y_us)
        c1_ok = abs(a["ke_usd"] - a["ke_fc"]) < 1e-12 and abs(a["wacc_usd"] - a["wacc_fc"]) < 1e-12 \
            and abs(a["kd_usd"] - a["kd_fc"]) < 1e-12
        b = compute(**{**base, "debt_share": 0.0}, y_fc=y_fc)
        c2_ok = abs(b["wacc_usd"] - b["ke_usd"]) < 1e-12 and abs(b["wacc_fc"] - b["ke_fc"]) < 1e-12
        c0 = compute(**base, y_fc=y_fc)
        c1x = compute(**base, y_fc=y_fc, spread_fc_override=0.05)
        c3_ok = all(abs(c0[k] - c1x[k]) < 1e-12 for k in ("ke_usd", "kd_usd", "wacc_usd"))
        checks = pd.DataFrame([
            ["Equal yields (y_FC = y_US), foreign-spread override off → identical results",
             "✅ pass" if c1_ok else "❌ fail",
             f"k_e {pct(a['ke_usd'], 4)} vs {pct(a['ke_fc'], 4)}; WACC {pct(a['wacc_usd'], 4)} vs {pct(a['wacc_fc'], 4)}"],
            ["Zero debt → WACC = cost of equity", "✅ pass" if c2_ok else "❌ fail",
             f"USD {pct(b['wacc_usd'], 4)} = {pct(b['ke_usd'], 4)}; {ccy} {pct(b['wacc_fc'], 4)} = {pct(b['ke_fc'], 4)}"],
            ["Overriding the foreign spread (to 5%) leaves USD results unchanged",
             "✅ pass" if c3_ok else "❌ fail",
             f"USD WACC {pct(c0['wacc_usd'], 4)} vs {pct(c1x['wacc_usd'], 4)}"],
            colombia_row(),
        ], columns=["Check", "Result", "Detail"])
        st.table(checks.set_index("Check"))

        st.subheader("Step-by-step for the current selection")
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
        if method_key == "additive":
            st.caption("Additive conversion selected: k_FC = k_USD + (y_FC − y_US).")

    # ---------------- Sources ----------------
    with tab_src:
        st.subheader("Data sources, dates and status")
        st.table(pd.DataFrame(source_rows(betas, bbb, taxes, ylds)).set_index("Data"))
        for name, d in [("Betas", betas), ("BBB spread", bbb), ("Tax rates", taxes), ("Yields", ylds)]:
            if d.get("error"):
                st.warning(f"{name}: {d['error']}")
        with st.expander("10Y government yields (Trading Economics)"):
            st.dataframe(y_df.assign(Yield=y_df["Yield"].map(lambda v: f"{v:.3%}")), width="stretch")
        with st.expander("Corporate tax rates (OECD)"):
            st.dataframe(tax_df.assign(**{"Tax rate": tax_df["Tax rate"].map(lambda v: f"{v:.2%}")}),
                         width="stretch")
        with st.expander("Industry unlevered betas (Damodaran)"):
            st.dataframe(beta_df, width="stretch")

    # ---------------- About ----------------
    with tab_about:
        st.markdown(ABOUT_MD)


def colombia_row():
    """Reproduce the in-class Colombia example (slide values)."""
    x = compute(0.80, 0.35, 0.30, 0.065, 0.04, 0.12, 0.015)
    target = dict(ke_usd=0.106486, wacc_usd=0.085265, ke_fc=0.191600, kd_fc=0.136154, wacc_fc=0.160670)
    ok = all(abs(x[k] - v) < 5e-7 for k, v in target.items())
    return ["Class example — Colombia (β_U 0.80, d 30%, τ 35%, ERP 6.5%, y 4%/12%, s 1.5%)",
            "✅ pass" if ok else "❌ fail",
            f"k_e {pct(x['ke_usd'],4)} / {pct(x['ke_fc'],4)}; k_d* {pct(x['kd_fc'],4)}; "
            f"WACC {pct(x['wacc_usd'],4)} / {pct(x['wacc_fc'],4)} (slides: 10.6486/19.1600; 13.6154; 8.5265/16.0670)"]


def source_rows(betas, bbb, taxes, ylds):
    y_df = ylds["df"]
    y_dates = ", ".join(sorted(set(y_df["Date"].astype(str)))[:3]) if not y_df.empty else ""
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
        {"Data": "10Y government bond yields", "Source": "Trading Economics — Bonds",
         "URL": TE_BONDS_URL, "Data as of": y_dates, "Fetched": ylds["fetched"],
         "Status": status_badge(ylds["status"])},
    ]


ABOUT_MD = r"""
### Method

1. **Levered beta (Hamada).** $\beta_L = \beta_U\,[1 + (1-t)\,D/E]$, with $D/E = w_d/(1-w_d)$.
   $\beta_U$ is Damodaran's U.S. industry *unlevered beta*.
2. **USD cost of equity (CAPM).** $k_e^{\$} = y_{US} + \beta_L \times ERP$.
3. **USD cost of debt.** $k_d^{\$} = y_{US} + s_{BBB}$, where $s_{BBB}$ is Damodaran's Baa2/BBB spread
   for large non-financial firms.
4. **Foreign-currency rates.** USD rates are translated using the 10-year government yield differential
   as a proxy for the expected inflation differential (international Fisher effect / relative PPP):
   $1 + k^{FC} = (1 + k^{\$})\,\dfrac{1 + y_{FC}}{1 + y_{US}}$.
   An additive approximation $k^{FC} \approx k^{\$} + (y_{FC} - y_{US})$ is available in the sidebar.
5. **Foreign borrowing spread.** By default the same factor $F = (1+y_{FC})/(1+y_{US})$ is applied:
   $s^* = s \times F$ and $k_d^{FC} = y_{FC} + s^*$ (equivalent to $(1+k_d^{\$})F - 1$).
   If the override is on, $s^*$ is the manual value; this affects only foreign-currency results.
6. **WACC.** $WACC = (1-w_d)\,k_e + w_d\,k_d\,(1-t)$, in each currency.

### Key assumptions
- The **same** tax rate (U.S. or selected country — toggle), debt share and ERP are used in both currencies.
- The project's risk is captured by the U.S. industry beta; no separate country-risk premium is added —
  country risk enters only through the local government yield (which can embed sovereign default risk).
- The 10Y government yield differential is treated as the expected inflation / currency-depreciation
  differential. This is a simplification: local yields can include a sovereign default spread and term premia.
- Tax rate = OECD *combined* (central + sub-central) statutory corporate income tax rate.
- Yields are scraped from Trading Economics; if the site is unreachable the U.S. yield falls back to FRED
  (DGS10) and other countries must be entered manually.
- Data are cached for up to 6 hours (yields: 1 hour). Use **Refresh data** to re-fetch.
"""


if __name__ == "__main__":
    main()
