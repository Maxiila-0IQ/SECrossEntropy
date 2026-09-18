import os
import shutil

MAX_HOURS = 24


class Settings:
    # GROQ_API_KEY: str = os.getenv(
    #     "GROQ_API_KEY", os.getenv("GROQCLOUD_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    # )
    # LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    # LLM_BASE_URL: str = os.getenv(
    #     "LLM_BASE_URL", "https://api.groq.com/openai/v1"
    # )
    GROQ_API_KEY: str = "not-needed"
    LLM_MODEL: str = "/models/Qwen3-14B-Q5_K_M.gguf"
    LLM_BASE_URL: str = "http://0.0.0.0:8080/v1"
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "8"))
    LLM_TEMPERATURE: float = 0.0
    LLM_USE_JSON_MODE: bool = os.getenv("LLM_USE_JSON_MODE", "1") != "0"

    SOLVER_BACKEND: str = os.getenv("SOLVER_BACKEND", "pulp")
    CBC_PATH: str | None = os.getenv("CBC_PATH") or shutil.which("cbc")

    RESERVE_PENALTY: float = 1e7
    GRID_CAP_PENALTY: float = 1e7
    NEUTRALITY_PENALTY: float = 1e9


settings = Settings()