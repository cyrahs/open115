from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = 'Open115'
    project_description: str = 'Open115 API'
    project_version: str = '0.1.0'
    api_prefix: str = '/v1'

    # Cache settings
    link_cache_ttl_seconds: int = 1800  # default 30 minutes

    # Cloudflare KV settings
    cf_account_id: str
    cf_kv_id: str
    cf_api_token: str
    
    # proxy_115cdn
    proxy_115cdn_host: str = '115cdn.s117.me'
    
    model_config = SettingsConfigDict(env_file='.env')


config = Settings()
