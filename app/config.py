import os
import shutil

MAX_HOURS = 24


class Settings:
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "15"))
    LLM_TEMPERATURE: float = 0.0

    SOLVER_BACKEND: str = os.getenv("SOLVER_BACKEND", "pulp")
    CBC_PATH: str | None = os.getenv("CBC_PATH") or shutil.which("cbc")

    RESERVE_PENALTY: float = 1e7
    GRID_CAP_PENALTY: float = 1e7
    NEUTRALITY_PENALTY: float = 1e9


settings = Settings()
