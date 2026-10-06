"""Environment + YAML config loading. Keys are read only on the server side."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


_load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str) -> bool:
    return env(name).lower() in ("1", "true", "yes", "on")


def _yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as fh:
        return yaml.safe_load(fh) or {}


class Settings:
    def __init__(self) -> None:
        self.demo_mode = env_bool("DEMO_MODE")
        # Public deployment: drop data whose licence forbids redistribution (see README "Going public")
        self.public_mode = env_bool("PUBLIC_MODE")
        self.public_market_ok = env_bool("MARKET_REDISTRIBUTION_LICENSED")
        self.public_acled_ok = env_bool("ACLED_PUBLIC_LICENSED")
        self.host = env("HOST", "127.0.0.1")
        self.port = int(env("PORT", "8700"))
        self.db_path = Path(env("DB_PATH", str(ROOT / "data" / "macro.db")))

        self.fred_key = env("FRED_API_KEY")
        self.eia_key = env("EIA_API_KEY")
        self.market_provider = env("MARKET_PROVIDER").lower()  # twelvedata | finnhub | polygon | ""
        self.market_tier = env("MARKET_TIER", f"{self.market_provider}_free" if self.market_provider else "")
        self.twelvedata_key = env("TWELVEDATA_API_KEY")
        self.finnhub_key = env("FINNHUB_API_KEY")
        self.polygon_key = env("POLYGON_API_KEY")
        self.aisstream_key = env("AISSTREAM_API_KEY")
        self.acled_email = env("ACLED_EMAIL")
        self.acled_password = env("ACLED_PASSWORD")

        self.main = _yaml("settings.yaml")
        self.kpis = _yaml("kpis.yaml")
        self.fred = _yaml("fred.yaml")
        self.metrics = _yaml("metrics.yaml")
        self.rules = _yaml("rules.yaml")
        self.readthrough = _yaml("readthrough.yaml")
        self.watchlist = _yaml("watchlist.yaml")
        self.chokepoints = _yaml("reference/chokepoints.yaml")["chokepoints"]
        self.infra = _yaml("reference/infrastructure.yaml")
        self.cb_calendar = _yaml("reference/cb_calendar.yaml")
        self.eco = _yaml("eco.yaml")
        self.ais = _yaml("ais.yaml")
        self.news = _yaml("news.yaml")
        self.securities = _yaml("securities.yaml").get("securities", [])
        self.credits = _yaml("credits.yaml")
        # entities fed only by licence-restricted series (hidden everywhere in public mode)
        self.restricted_entities = {s["entity"] for s in self.fred.get("series", []) if s.get("license") == "restricted"}

    def hidden(self, entity: str) -> bool:
        return self.public_mode and entity in self.restricted_entities

    def provider(self, name: str) -> dict:
        cfg = dict(self.main["providers"].get(name, {}))
        if name == "MARKET" and self.market_tier in self.main.get("market_tiers", {}):
            cfg.update(self.main["market_tiers"][self.market_tier])
        return cfg

    def reload_opec_quotas(self) -> list:
        return (_yaml("reference/opec_quotas.yaml") or {}).get("quotas") or []


SETTINGS = Settings()
