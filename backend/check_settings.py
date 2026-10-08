from app.config.settings import get_settings

s = get_settings()
print(f'webhook_secret: "{s.github_webhook_secret}"')
print(f'github_pat: "{s.github_pat}"')
print(f"database_url: {s.database_url}")
