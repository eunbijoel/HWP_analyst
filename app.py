"""
Compat entry — Product A (HWP Document Intelligence).

Preferred:  streamlit run apps/intelligence/app.py
Also works: streamlit run app.py
"""

from pathlib import Path
import runpy

_TARGET = Path(__file__).resolve().parent / "apps" / "intelligence" / "app.py"
runpy.run_path(str(_TARGET), run_name="__main__")
