"""Adapter registry. Add new providers here (see base.py for the contract)."""
from .acled import AcledAdapter
from .aisstream import AisStreamAdapter
from .bis import BisAdapter
from .demo import DemoAdapter
from .digitraffic import DigitrafficAdapter
from .eia import EiaAdapter
from .fred import FredAdapter
from .gdacs import GdacsAdapter
from .gdelt import GdeltAdapter
from .jodi import JodiAdapter
from .market import MarketAdapter
from .news_rss import NewsAdapter
from .opec_cfg import OpecConfigAdapter
from .portwatch import PortWatchAdapter
from .worldbank import WorldBankAdapter

ALL = [MarketAdapter, FredAdapter, EiaAdapter, JodiAdapter, OpecConfigAdapter, PortWatchAdapter,
       BisAdapter, WorldBankAdapter, GdeltAdapter, AcledAdapter, GdacsAdapter, DigitrafficAdapter,
       AisStreamAdapter, NewsAdapter, DemoAdapter]
