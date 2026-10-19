"""CSV-driven enum container for classes and majors.

Loaded once at startup from data/*.csv files.
Modify enum = edit CSV + restart (no code change needed).
"""
import pandas as pd
from core.config import settings


class EnumContainer:
    _classes: list[str] = []
    _majors: list[str] = []

    @classmethod
    def load(cls):
        classes_csv = settings.DATA_DIR / "班级.csv"
        majors_csv = settings.DATA_DIR / "专业.csv"

        if classes_csv.exists():
            df = pd.read_csv(classes_csv, header=None, names=["name"])
            cls._classes = df["name"].dropna().tolist()

        if majors_csv.exists():
            df = pd.read_csv(majors_csv, header=None, names=["name"])
            cls._majors = df["name"].dropna().tolist()

    @classmethod
    def get_classes(cls) -> list[str]:
        return cls._classes

    @classmethod
    def get_majors(cls) -> list[str]:
        return cls._majors

    @classmethod
    def is_valid_class(cls, name: str) -> bool:
        return name in cls._classes

    @classmethod
    def is_valid_major(cls, name: str) -> bool:
        return name in cls._majors
