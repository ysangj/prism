"""UI helper package for the Prism Streamlit app.

Keeps `app.py` readable by housing:
  - `config`: per-product-type form field definitions + unit conversions
  - `charts`: Plotly figure builders for decomposition, payoff, distribution
  - `formatting`: currency/percent display helpers

None of this touches the `prism/` engine package; it only consumes its public
contract (see BACKEND_NOTES.md).
"""
