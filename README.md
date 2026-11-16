# 🌍 International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of any selected country**, using live market and reference data.

**Live app:** _<add your Streamlit Cloud URL>_

## Features
- **Live data, with sources and dates shown** (cached; 🔄 *Refresh data* button re-fetches everything)
  | Input | Source |
  |---|---|
  | Industry unlevered betas (U.S.) | Aswath Damodaran, [betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls), column *Unlevered beta* |
  | USD BBB spread | Damodaran, [ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html), Baa2/BBB, large non-financial service firms |
  | Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html), combined statutory CIT rate, pulled from the OECD Data API (`DSD_TAX_CIT@DF_CIT`), latest year |
  | 10-year government yields | [Trading Economics – Bonds](https://tradingeconomics.com/bonds) (scraped, no API key). The bonds table is loaded from a bot-protected endpoint, so if it's blocked the app reads the same quote from each country's page, e.g. `tradingeconomics.com/colombia/government-bond-yield` |
- **Inputs:** industry, target country, equity risk premium, target debt share.
- **Type-over inputs:** every data field shows the fetched value in grey (beta, U.S. and foreign tax rates, U.S. and foreign 10Y yields, USD BBB spread). Typing a number overrides it; clearing the field returns to the fetched value. The foreign-currency BBB spread shows the automatic value s × F until a manual value is typed.
- ERP and debt share are typed directly.
- **Tax-rate switch:** use the U.S. or the selected country's rate. The same rate applies in both currencies.
- **Side-by-side results** (USD vs. local currency), a step-by-step calculation table, an **all-industry comparison**, and **downloads** (CSV and a full Excel workbook). The Results tab also has a *Calculation steps* section showing every intermediate number.

**Defaults:** Software (System & Application) · United States · U.S. tax rate · ERP 6.5% · 30% debt.

## Method
| Step | Formula |
|---|---|
| Debt-to-equity | D/E = w_d / (1 − w_d) |
| Levered beta (Hamada) | β_L = β_U × [1 + (1 − t) × D/E] |
| USD cost of equity (CAPM) | k_e$ = y_US + β_L × ERP |
| USD cost of debt | k_d$ = y_US + s_BBB |
| Foreign-currency rate | 1 + k_FC = (1 + k$) × (1 + y_FC) / (1 + y_US) |
| Foreign borrowing spread | auto: s* = s × F, with F = (1 + y_FC)/(1 + y_US); or manual override |
| Foreign cost of debt | k_d,FC = y_FC + s* |
| WACC | (1 − w_d) × k_e + w_d × k_d × (1 − t) |

### Assumptions
- One tax rate, debt share and ERP are shared across both currencies.
- Project risk comes from the U.S. industry beta. There is no separate country-risk premium; country risk enters through the local government yield.
- The 10Y yield differential is used as a proxy for the expected inflation/depreciation differential. Local yields may also contain sovereign default and term premia.
- If a source can't be reached, the app says so: the BBB spread and tax rates fall back to a dated snapshot, the U.S. yield falls back to FRED (DGS10), and other inputs can be entered manually.

## Verification
0. Reproduces the in-class Colombia example: k_e 10.6486% / 19.1600%, k_d* 13.6154%, WACC 8.5265% / 16.0670%.
1. Equal government yields with the foreign-spread override off give identical USD and foreign results.
2. Zero debt makes WACC equal to the cost of equity.
3. Overriding the foreign-currency spread leaves all USD results unchanged.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Push `app.py`, `requirements.txt` and `README.md` to a public GitHub repo, then go to [share.streamlit.io](https://share.streamlit.io), choose **New app**, and select the repo with `app.py` as the main file.

_Built with AI assistance (Claude). All calculations were independently verified._
