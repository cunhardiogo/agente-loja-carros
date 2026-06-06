from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_role: str

    openai_api_key: str = ""
    openai_model_extracao: str = "gpt-4.1-mini"
    openai_model_consulta: str = "gpt-4.1"

    evolution_url: str = ""
    # Coletor: lê os grupos (diogo4895)
    evolution_instance: str = ""
    evolution_apikey: str = ""
    # Assistente: manda relatórios e responde o dono (2º chip)
    evolution_assist_instance: str = ""
    evolution_assist_apikey: str = ""

    meu_numero: str = ""
    confianca_minima: float = 0.8
    verify_ssl: bool = True
    dashboard_token: str = ""
    planilha_csv_url: str = ("https://docs.google.com/spreadsheets/d/"
                             "1_lxkBFmqyI1JAKWVrdvZHvFoqSoaqsijjIq-WKdy5Ec/export?format=csv")


settings = Settings()
