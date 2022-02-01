import secrets
from typing import Optional

import pydantic as pd
from urllib3.util import parse_url

from .defs import Env


class EnvMixin(pd.BaseSettings):
    # Environment
    ENV: Env

    @property
    def is_production(self) -> bool:
        """ Is running in a production environment?

        This normally means stricter checks, absence of development tools & debug information
        """
        return self.ENV == Env.PROD

    @property
    def is_testing(self) -> bool:
        """ Is running in testing environment?

        This normally means more debug code is executed
        """
        return self.ENV == Env.TEST

    @property
    def is_development(self) -> bool:
        """ Is running in a development environment?

        This normally means that dev tools and tracebacks are available
        """
        return self.ENV == Env.DEV


class LocaleMixin(pd.BaseSettings):
    # Locale
    LOCALE: str = 'en'

    # Default timezone
    TZ: str = 'Europe/Moscow'


class DomainMixin(pd.BaseSettings):
    # URL to where the app is served
    # Example: https://example.com/
    SERVER_URL: pd.AnyHttpUrl

    @property
    def DOMAIN(self):
        """ Domain name: host:port """
        purl = parse_url(self.SERVER_URL)
        return purl.netloc


class CorsMixin(pd.BaseSettings):
    # Allowed CORS origins
    # List of JSON urls: ["http://localhost","http://localhost:4200"]
    CORS_ORIGINS: list[pd.AnyHttpUrl] = []


    @pd.validator('CORS_ORIGINS', pre=True)
    def prepare_cors_origins(cls, v: Optional[str]):
        if isinstance(v, str):
            return [i.strip() for i in v.split(',')]
        return v

    def __init__(self, *args, **kwargs):
        # NOTE: because @validator(pre=True) isn't executed early enough with environment settings
        # (it already tries to load JSON from the value into a complex field), we have to apply a hack here
        # See: https://github.com/samuelcolvin/pydantic/issues/1458
        import os, json
        CORS_NAME = self.Config.env_prefix + 'CORS_ORIGINS'
        if f'{CORS_NAME}::modified' not in os.environ:
            os.environ[CORS_NAME] = json.dumps(
                Settings.prepare_cors_origins(os.getenv(CORS_NAME, ''))
            )

            # We have to make sure that this os.environ hacking happens only once.
            # Otherwise, uvicorn reloader may re-run it and corrupt the variable value
            os.environ[f'{CORS_NAME}::modified'] = '1'

        # Parse the settings from environment
        super().__init__(*args, **kwargs)


class SecretMixin(pd.BaseSettings):
    # Secret key for the app
    # The default is used for testing and is regenerated every time
    SECRET_KEY: str = secrets.token_urlsafe(32)


class Settings(pd.BaseSettings, EnvMixin, LocaleMixin, DomainMixin, CorsMixin, SecretMixin):
    # Human-readable name of this project
    # Used in titles & emails & stuff
    PROJECT_NAME: str

    class Config:
        # env_prefix = 'APP_'  # you can set a prefix for your environment variables
        case_sensitive = True


class PostgresMixin(pd.BaseSettings):
    # Database connection
    # Names of these variables match with names from the postgres Docker container.
    # We manually set `env=` name to make sure that Config.env_prefix has no effect on it
    POSTGRES_HOST: str = pd.Field(..., env='POSTGRES_HOST')
    POSTGRES_PORT: str = pd.Field(..., env='POSTGRES_PORT')
    POSTGRES_USER: str = pd.Field(..., env='POSTGRES_USER')
    POSTGRES_PASSWORD: str = pd.Field(..., env='POSTGRES_PASSWORD')
    POSTGRES_DB: str = pd.Field(..., env='POSTGRES_DB')
    POSTGRES_URL: Optional[pd.PostgresDsn] = None  # build automatically

    @pd.validator("POSTGRES_URL", pre=True)
    def prepare_postgres_url(cls, v: Optional[str], values: dict):
        assert not v, 'This value should not be set directly'

        return pd.PostgresDsn.build(
            scheme="postgresql",
            user=values.get("POSTGRES_USER"),
            password=values.get("POSTGRES_PASSWORD"),
            host=values.get("POSTGRES_HOST"),
            path=f"/{values.get('POSTGRES_DB') or ''}",
        )
