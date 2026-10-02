"""Shared risk metadata; classification alone never grants authority."""

from enum import StrEnum


class RiskClass(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    POLICY_CONTROLLED = "POLICY_CONTROLLED"
    STRICT_AUTHORIZATION = "STRICT_AUTHORIZATION"
