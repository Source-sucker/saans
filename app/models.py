"""Pydantic request/response models with strict limits (security)."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

CITY_RE = re.compile(r"^[A-Za-zÀ-ÿ' .,-]{1,80}$")


class Member(BaseModel):
    label: str = Field(max_length=40)
    age_years: int | None = Field(default=None, ge=0, le=120)
    sensitive: bool = False


class Home(BaseModel):
    purifier: bool = False
    unvented_combustion: bool = False
    cooking_fuel: str | None = Field(default=None, pattern="^(lpg|png|gas|biomass|induction|electric)$")


class Commute(BaseModel):
    mode: str | None = Field(default=None, pattern="^(walk|cycle|two_wheeler|car|metro|bus)$")
    km: float | None = Field(default=None, ge=0, le=500)
    carpool_passengers: int = Field(default=2, ge=2, le=6)


class Profile(BaseModel):
    members: list[Member] = Field(default_factory=list, max_length=8)
    home: Home = Field(default_factory=Home)
    commute: Commute = Field(default_factory=Commute)

    @field_validator("members")
    @classmethod
    def members_capped(cls, v: list[Member]) -> list[Member]:
        if len(v) > 8:
            raise ValueError("at most 8 members")
        return v


class PlanRequest(BaseModel):
    city: str = Field(min_length=1, max_length=80)
    profile: Profile = Field(default_factory=Profile)

    @field_validator("city")
    @classmethod
    def city_charset(cls, v: str) -> str:
        if not CITY_RE.match(v):
            raise ValueError("city: letters, spaces, apostrophes, hyphens, commas only")
        return v.strip()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    # plan context is re-derived server-side from city+profile; never trusted from client
    city: str = Field(min_length=1, max_length=80)
    profile: Profile = Field(default_factory=Profile)


class ManualAQIRequest(BaseModel):
    pm25: float | None = Field(default=None, ge=0, le=1000)
    pm10: float | None = Field(default=None, ge=0, le=2000)
    city: str = Field(min_length=1, max_length=80)
    profile: Profile = Field(default_factory=Profile)

    @field_validator("city")
    @classmethod
    def city_charset(cls, v: str) -> str:
        if not CITY_RE.match(v):
            raise ValueError("city: letters, spaces, apostrophes, hyphens, commas only")
        return v.strip()
