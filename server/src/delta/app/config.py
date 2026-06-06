from typing import Literal

from dotenv import find_dotenv
from pydantic import AnyUrl, EmailStr, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=find_dotenv(), extra="ignore")


class AppSettings(BaseAppSettings):
    """Global app settings. These are generic app level settings"""

    app_env: Literal["dev", "local", "prod"] = "dev"

    # logging related
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json_enabled: bool = True
    log_json_path: str = "logs/app.jsonl"
    log_rotation: str = "100 MB"
    log_retention: str = "30 days"
    log_compression: str = "zip"
    log_console_enabled: bool = True
    # TODO: add other fields as and when they are put in the .env file


class LLMSettings(BaseAppSettings):
    """LLM related settings. API keys, endpoints etc"""

    # model providers
    # nebius
    nebius_api_key: SecretStr | None = None
    nebius_api_endpoint: AnyUrl | None = None
    # hugginface
    hf_api_key: SecretStr | None = None
    hf_api_endpoint: AnyUrl | None = None
    # add observability later
    # langfuse_public_key: SecretStr
    # langfuse_secret_key: SecretStr
    # langfuse_host: AnyUrl


class StorageSettings(BaseAppSettings):
    """All database and storage related settings. Postgres, redis, object storage are must"""

    # postgres
    database_url: PostgresDsn
    database_url_direct: PostgresDsn
    # redis
    upstash_redis_rest_url: AnyUrl
    upstash_redis_rest_token: SecretStr
    # local redis
    # todo
    # r2
    r2_account_id: SecretStr
    r2_token_value: SecretStr
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr
    r2_endpoint_url: AnyUrl
    r2_bucket_name: str


class SearchSettings(BaseAppSettings):
    """All paper and web search related stuff"""

    # web
    # exa
    # exa_api_key: SecretStr
    # tavily
    tavily_api_key: SecretStr
    tavily_mcp_url: SecretStr
    # serpapi
    serpapi_api_key: SecretStr
    # todo add the rest

    # papers
    openalex_api_key: SecretStr
    openalex_email_id: EmailStr
    # todo add the rest


class AuthSettings(BaseAppSettings):
    """All auth related settings go here"""

    # clerk
    # sentry
    pass


app_settings = AppSettings()
llm_settings = LLMSettings()
storage_settings = StorageSettings()
auth_settings = AuthSettings()
search_settings = SearchSettings()

__all__ = ["app_settings", "llm_settings", "storage_settings", "auth_settings", "search_settings"]
