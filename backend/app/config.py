"""Runtime configuration (environment / .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: python-dotenv is in requirements, but never required to import
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parents[2]
if load_dotenv:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / "backend" / ".env")

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


@dataclass
class Settings:
    workspace: Path = field(default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", REPO_ROOT / "workspace")))
    samples_dir: Path = field(default_factory=lambda: REPO_ROOT / "samples")
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq").lower())
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip())
    groq_base_url: str = field(default_factory=lambda: os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    max_repair_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_REPAIR_ATTEMPTS", "3")))
    # client-side throttle; set these to your Groq plan's limits for the chosen model
    groq_rpm: int = field(default_factory=lambda: int(os.getenv("GROQ_RPM", "25")))
    groq_tpm: int = field(default_factory=lambda: int(os.getenv("GROQ_TPM", "12000")))
    env_file: Path = field(default_factory=lambda: REPO_ROOT / ".env")

    def public(self) -> dict:
        return {
            "llm_provider": self.llm_provider,
            "llm_configured": bool(self.groq_api_key) and self.llm_provider == "groq",
            "model": self.groq_model,
            "key_hint": ("…" + self.groq_api_key[-4:]) if len(self.groq_api_key) >= 8 else ("set" if self.groq_api_key else ""),
            "max_repair_attempts": self.max_repair_attempts,
            "rpm": self.groq_rpm,
            "tpm": self.groq_tpm,
        }


settings = Settings()


def persist_env(values: dict[str, str]) -> None:
    """Create/update KEY=value lines in the .env file, preserving everything else."""
    path = settings.env_file
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining and not line.lstrip().startswith("#"):
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
