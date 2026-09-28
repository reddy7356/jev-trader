from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskConfig(BaseModel):
    """Hard risk limits. Absolute veto, never delegated to the model."""

    model_config = ConfigDict(extra="ignore")

    max_position: float = 100.0
    max_order_size: float = 25.0
    max_daily_loss: float = 250.0
    max_drawdown: float = 0.05
    max_inventory_age_s: float = 900.0
    max_stale_data_age_s: float = 2.0
    max_decision_latency_ms: float = 250.0
    max_api_errors: int = 5  # consecutive
    kill_position_multiplier: float = 1.5


class PolicyConfig(BaseModel):
    """Policy thresholds. They live in code, not in the model."""

    model_config = ConfigDict(extra="ignore")

    toxic_pull: float = 0.60
    liquidity_widen: float = 0.70
    quote_min_score: float = 2.0
    quote_min_confidence: float = 0.80
    quote_wide_min_score: float = 1.0
    low_confidence_scale: float = 0.5
    wide_spread_multiplier: float = 2.0
    kelly_cap: float = 0.25


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gamma: float = 0.5
    kappa: float = 100.0
    horizon_s: float = 60.0
    min_half_spread_bps: float = 1.0
    tick_size: float = 0.01
    # keep a resting quote if the new target price is within this many ticks
    requote_ticks: float = 0.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JEV_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    typesafe_api_key: SecretStr | None = Field(
        default=None, validation_alias="TYPESAFE_API_KEY"
    )
    # Slack/Discord-compatible webhook for operator alerts (optional; alerts always log)
    alert_webhook_url: str | None = Field(default=None, validation_alias="ALERT_WEBHOOK_URL")
    # frontier-LLM baseline in scripts/backtest.py only
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    # pin the versioned ID, not the jev-latest alias: thresholds are tuned per version
    jev_model: str = "jev-1.13.0"
    # input tokens only; output is free (docs.typesafe.ai/models)
    jev_usd_per_mtok: float = 0.042
    # Hyperliquid testnet: an API (agent) wallet key signs; the main account is queried
    hl_private_key: SecretStr | None = Field(default=None, validation_alias="HL_PRIVATE_KEY")
    hl_account_address: str | None = Field(default=None, validation_alias="HL_ACCOUNT_ADDRESS")
    block_ms: int = 300
    decision_timeout_s: float = 0.25
    calibration_horizon_blocks: int = 10
    log_path: Path = Path("data/calibration.jsonl")
    starting_cash: float = 10_000.0
    fee_bps: float = 1.0
    order_ack_ms: float = 0.0
    cancel_ms: float = 0.0
    gas_per_place: float = 0.0
    gas_per_cancel: float = 0.0

    risk: RiskConfig = Field(default_factory=RiskConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)

    @property
    def has_api_key(self) -> bool:
        return self.typesafe_api_key is not None and bool(
            self.typesafe_api_key.get_secret_value()
        )
