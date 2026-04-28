"""
Pydantic request/response models and validation helpers.

Requirements: 9.4, 9.5
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthParams(BaseModel):
    dengue: bool = False
    covid: bool = False
    vaccination: bool = False
    internacoes: bool = False
    mortalidade: bool = False


class AnalysisRequest(BaseModel):
    dateFrom: int = 2019
    dateTo: int = 2021
    healthParams: HealthParams
    useLlm: bool = True


class AnalysisResponse(BaseModel):
    analysisId: str


def validate_analysis_params(req: AnalysisRequest) -> list[str]:
    """Return a list of validation error messages (empty == valid)."""
    errors: list[str] = []
    if req.dateFrom > req.dateTo:
        errors.append("dateFrom must be <= dateTo")
    hp = req.healthParams
    if not (hp.dengue or hp.covid or hp.vaccination or hp.internacoes or hp.mortalidade):
        errors.append("At least one healthParam must be true")
    return errors


def health_params_to_list(hp: HealthParams) -> list[str]:
    """Convert HealthParams booleans to a list of type strings."""
    params: list[str] = []
    if hp.dengue:
        params.append("dengue")
    if hp.covid:
        params.append("covid")
    if hp.vaccination:
        params.append("vacinacao")
    if hp.internacoes:
        params.append("internacoes")
    if hp.mortalidade:
        params.append("mortalidade")
    return params
