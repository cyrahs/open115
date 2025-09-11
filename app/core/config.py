from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    project_name: str = 'Open115'
    project_description: str = 'Open115 API'
    project_version: str = '0.1.0'
    api_prefix: str = '/v1'

    cf_account_id: str
    cf_kv_id: str
    cf_api_token: str

    class Config:
        env_file = '.env'


config = Settings()