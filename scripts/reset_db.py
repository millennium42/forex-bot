"""Script de reset do banco de dados. Encerra todas as sessões ativas e recria o DB do zero."""

import psycopg

ADMIN_URL = "postgresql://forex:forex@127.0.0.1:5432/postgres"
DB_NAME = "forex_bot"
DB_OWNER = "forex"


def main() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        # Encerra todas as conexões ativas no banco alvo
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (DB_NAME,),
        )
        print(f"Sessoes ativas em '{DB_NAME}' encerradas.")

        conn.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
        print(f"Banco '{DB_NAME}' removido.")

        conn.execute(f"CREATE DATABASE {DB_NAME} OWNER {DB_OWNER}")
        print(f"Banco '{DB_NAME}' recriado.")


if __name__ == "__main__":
    main()
