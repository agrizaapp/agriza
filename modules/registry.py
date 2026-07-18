MODULES = {
    "painel": {"label": "🏠 Painel", "roles": {"admin", "operador", "consulta"}},
    "safras": {"label": "🌾 Safras", "roles": {"admin", "operador", "consulta"}},
    "compras": {"label": "🛒 Compras", "roles": {"admin", "operador", "consulta"}},
    "vendas": {"label": "💰 Vendas", "roles": {"admin", "operador", "consulta"}},
    "mercado": {"label": "📈 Mercado", "roles": {"admin", "operador", "consulta"}},
    "usuarios": {"label": "👥 Usuários", "roles": {"admin"}},
    "backup": {"label": "📦 Backup", "roles": {"admin"}},
}

def enabled_pages(role: str) -> list[str]:
    return [item["label"] for item in MODULES.values() if role in item["roles"]]
