import argparse
import json
import pymysql


def data_type_mod(row):
    if row["CHARACTER_MAXIMUM_LENGTH"] is not None:
        return int(row["CHARACTER_MAXIMUM_LENGTH"])
    if row["NUMERIC_PRECISION"] is not None:
        return int(row["NUMERIC_PRECISION"])
    return 0


def data_distribution(row):
    t = row["DATA_TYPE"].lower()
    if t in {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "decimal", "numeric", "float", "double"}:
        return [0, 0]
    if row["CHARACTER_MAXIMUM_LENGTH"] is not None:
        n = int(row["CHARACTER_MAXIMUM_LENGTH"])
        return [0, n]
    return [0, 0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    conn = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        cursorclass=pymysql.cursors.DictCursor,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                TABLE_NAME,
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            (args.database,),
        )
        columns = cur.fetchall()

        cur.execute(
            """
            SELECT
                k.TABLE_NAME,
                k.COLUMN_NAME,
                c.DATA_TYPE
            FROM information_schema.TABLE_CONSTRAINTS t
            JOIN information_schema.KEY_COLUMN_USAGE k
              ON t.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA
              AND t.TABLE_NAME = k.TABLE_NAME
              AND t.CONSTRAINT_NAME = k.CONSTRAINT_NAME
            JOIN information_schema.COLUMNS c
              ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
              AND c.TABLE_NAME = k.TABLE_NAME
              AND c.COLUMN_NAME = k.COLUMN_NAME
            WHERE t.CONSTRAINT_SCHEMA = %s
              AND t.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ORDER BY k.TABLE_NAME, k.ORDINAL_POSITION
            """,
            (args.database,),
        )
        pk_rows = cur.fetchall()

        cur.execute(
            """
            SELECT
                TABLE_NAME,
                COLUMN_NAME,
                REFERENCED_TABLE_NAME,
                REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_NAME, COLUMN_NAME
            """,
            (args.database,),
        )
        fk_rows = cur.fetchall()

    conn.close()

    pk_map = {}
    for row in pk_rows:
        pk_map.setdefault(row["TABLE_NAME"], []).append(row)

    fk_map = {}
    for row in fk_rows:
        fk_map.setdefault(row["TABLE_NAME"], []).append(row)

    table_map = {}
    for row in columns:
        table_map.setdefault(row["TABLE_NAME"], []).append(row)

    tables = []
    for table_name, cols in table_map.items():
        table_columns = []
        for col in cols:
            table_columns.append(
                {
                    "Column Name": col["COLUMN_NAME"],
                    "Data Type": col["DATA_TYPE"],
                    "Data Type Mod": data_type_mod(col),
                    "Data Distribution": data_distribution(col),
                }
            )

        col_count = len(table_columns)
        column_distribution = [1.0 / col_count for _ in range(col_count)] if col_count else []

        pk_list = pk_map.get(table_name, [])
        if pk_list:
            primary_key = {
                "Name": pk_list[0]["COLUMN_NAME"],
                "Data Type": pk_list[0]["DATA_TYPE"],
            }
        else:
            primary_key = {
                "Name": "",
                "Data Type": "",
            }

        foreign_keys = []
        for fk in fk_map.get(table_name, []):
            foreign_keys.append(
                {
                    "Foreign Key Name": fk["COLUMN_NAME"],
                    "Foreign Key Type": "",
                    "Referenced Table": fk["REFERENCED_TABLE_NAME"],
                    "Referenced Primary Key": fk["REFERENCED_COLUMN_NAME"],
                    "Referenced Primary Key Type": "",
                }
            )

        if not foreign_keys:
            foreign_keys = [
                {
                    "Foreign Key Name": "",
                    "Foreign Key Type": "",
                    "Referenced Table": "",
                    "Referenced Primary Key": "",
                    "Referenced Primary Key Type": "",
                }
            ]

        tables.append(
            {
                "Table Name": table_name,
                "Table Columns": table_columns,
                "Column Distribution": column_distribution,
                "Primary Key": primary_key,
                "Foreign Key": foreign_keys,
            }
        )

    result = {
        "Table Schema": args.database,
        "Tables": tables,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)

    print(f"wrote {len(tables)} tables to {args.output}")


if __name__ == "__main__":
    main()
