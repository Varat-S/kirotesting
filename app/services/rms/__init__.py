"""Risk Mitigation & Structuring (RMS): a deterministic loan verdict and structure from the BO and FO outputs.

FO sizes the loan (debt capacity from Debt/EBITDAR and DSCR, base and downside); BO cuts it down (the M4 credit verdict
notches the grade, M3 structural findings and unproven mitigants are a haircut); the FO composite notched by BO
sets the grade; grade x utilisation (requested / capacity) gives the verdict; the signals that fired give the
mitigants. No LLM. See ``verdict.assess``.
"""

from __future__ import annotations

from .inputs import BoInputs, FoInputs, RmsRequest, load_bo, load_fo
from .verdict import RmsResult, assess

__all__ = ["assess", "RmsRequest", "RmsResult", "FoInputs", "BoInputs", "load_fo", "load_bo"]
