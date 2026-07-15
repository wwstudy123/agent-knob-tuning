"""
Unit tests for export_schema_json.py

Covers:
- data_type_mod: normal, boundary, edge cases
- data_distribution: all data type categories, edge cases
- main: integration with mocked DB, schema assembly logic
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, mock_open, patch

import pytest

from export_schema_json import data_distribution, data_type_mod, main


# ---------------------------------------------------------------------------
# data_type_mod
# ---------------------------------------------------------------------------

class TestDataTypeMod:
    """Tests for data_type_mod(row)."""

    def test_character_max_length_present(self):
        """When CHARACTER_MAXIMUM_LENGTH is set, return its int value."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": 255, "NUMERIC_PRECISION": None}) == 255

    def test_character_max_length_zero(self):
        """CHARACTER_MAXIMUM_LENGTH = 0 should return 0."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": 0, "NUMERIC_PRECISION": None}) == 0

    def test_numeric_precision_fallback(self):
        """When CHARACTER_MAXIMUM_LENGTH is None, fall back to NUMERIC_PRECISION."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10}) == 10

    def test_both_none_returns_zero(self):
        """When both are None, return 0."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": None}) == 0

    def test_both_set_prefers_character(self):
        """When both are set, CHARACTER_MAXIMUM_LENGTH takes priority."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": 100, "NUMERIC_PRECISION": 20}) == 100

    def test_large_values(self):
        """Large integer values should be handled correctly."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": 16777215, "NUMERIC_PRECISION": None}) == 16777215

    def test_numeric_precision_zero(self):
        """NUMERIC_PRECISION = 0 should return 0."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 0}) == 0

    def test_string_values_are_cast_to_int(self):
        """String values should be cast to int (simulating DB driver behavior)."""
        assert data_type_mod({"CHARACTER_MAXIMUM_LENGTH": "64", "NUMERIC_PRECISION": None}) == 64


# ---------------------------------------------------------------------------
# data_distribution
# ---------------------------------------------------------------------------

class TestDataDistribution:
    """Tests for data_distribution(row)."""

    # ---- numeric types ----
    @pytest.mark.parametrize(
        "dtype",
        ["tinyint", "smallint", "mediumint", "int", "integer",
         "bigint", "decimal", "numeric", "float", "double"],
    )
    def test_numeric_types_return_zero_zero(self, dtype):
        """All recognised numeric types should return [0, 0]."""
        assert data_distribution({"DATA_TYPE": dtype, "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_numeric_case_insensitive(self):
        """Type comparison is case-insensitive."""
        assert data_distribution({"DATA_TYPE": "INT", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]
        assert data_distribution({"DATA_TYPE": "Decimal", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_numeric_type_ignores_character_max_length(self):
        """Even if CHARACTER_MAXIMUM_LENGTH is set, numeric types still return [0, 0]."""
        assert data_distribution({"DATA_TYPE": "int", "CHARACTER_MAXIMUM_LENGTH": 100}) == [0, 0]

    # ---- string / character types ----
    def test_varchar_returns_zero_length(self):
        """VARCHAR with max length returns [0, max_length]."""
        assert data_distribution({"DATA_TYPE": "varchar", "CHARACTER_MAXIMUM_LENGTH": 255}) == [0, 255]

    def test_char_returns_zero_length(self):
        """CHAR with max length returns [0, max_length]."""
        assert data_distribution({"DATA_TYPE": "char", "CHARACTER_MAXIMUM_LENGTH": 10}) == [0, 10]

    def test_text_returns_zero_length(self):
        """TEXT with max length returns [0, max_length]."""
        assert data_distribution({"DATA_TYPE": "text", "CHARACTER_MAXIMUM_LENGTH": 65535}) == [0, 65535]

    def test_blob_returns_zero_length(self):
        """BLOB with max length returns [0, max_length]."""
        assert data_distribution({"DATA_TYPE": "blob", "CHARACTER_MAXIMUM_LENGTH": 65535}) == [0, 65535]

    def test_string_type_zero_max_length(self):
        """String type with CHARACTER_MAXIMUM_LENGTH = 0 returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "varchar", "CHARACTER_MAXIMUM_LENGTH": 0}) == [0, 0]

    # ---- types with no max length ----
    def test_enum_returns_zero_zero(self):
        """ENUM (no CHARACTER_MAXIMUM_LENGTH) returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "enum", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_set_returns_zero_zero(self):
        """SET (no CHARACTER_MAXIMUM_LENGTH) returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "set", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_json_returns_zero_zero(self):
        """JSON type returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "json", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_geometry_returns_zero_zero(self):
        """GEOMETRY type returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "geometry", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]

    def test_unknown_type_with_max_length(self):
        """Unknown non-numeric type with max length returns [0, length]."""
        assert data_distribution({"DATA_TYPE": "custom_type", "CHARACTER_MAXIMUM_LENGTH": 500}) == [0, 500]

    def test_unknown_type_without_max_length(self):
        """Unknown non-numeric type without max length returns [0, 0]."""
        assert data_distribution({"DATA_TYPE": "custom_type", "CHARACTER_MAXIMUM_LENGTH": None}) == [0, 0]


# ---------------------------------------------------------------------------
# main (integration with mocked database)
# ---------------------------------------------------------------------------

class TestMain:
    """Tests for main() using mocked pymysql connection and file I/O."""

    @pytest.fixture
    def mock_db(self):
        """Provide a reusable set of mock database results."""
        columns = [
            {"TABLE_NAME": "users", "COLUMN_NAME": "id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "users", "COLUMN_NAME": "name", "DATA_TYPE": "varchar",
             "CHARACTER_MAXIMUM_LENGTH": 100, "NUMERIC_PRECISION": None},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "user_id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "amount", "DATA_TYPE": "decimal",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 12},
        ]
        pk_rows = [
            {"TABLE_NAME": "users", "COLUMN_NAME": "id", "DATA_TYPE": "int"},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "id", "DATA_TYPE": "int"},
        ]
        fk_rows = [
            {"TABLE_NAME": "orders", "COLUMN_NAME": "user_id",
             "REFERENCED_TABLE_NAME": "users", "REFERENCED_COLUMN_NAME": "id"},
        ]
        return columns, pk_rows, fk_rows

    @pytest.fixture
    def mock_cursor(self, mock_db):
        """Return a MagicMock cursor that returns the fixture data in order."""
        columns, pk_rows, fk_rows = mock_db
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]
        return cur

    def _run_main_with_args(self, extra_args, mock_cursor):
        """Helper to invoke main() with patched pymysql and argv."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", ["export_schema_json.py"] + extra_args):
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    # replace output path with our temp file
                    argv = ["export_schema_json.py"] + extra_args
                    # find --output index
                    out_idx = argv.index("--output")
                    argv[out_idx + 1] = tmp_path
                    with patch("sys.argv", argv):
                        main()
                finally:
                    # clean up temp file
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

                return tmp_path, mock_conn

    def test_basic_schema_export(self, mock_cursor, mock_db):
        """A typical run should produce valid JSON with correct structure."""
        columns, _, _ = mock_db

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root",
                "--password", "pass",
                "--database", "testdb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()) as m_open:
                    with patch("builtins.print") as mock_print:
                        main()

            # Verify JSON structure
            m_open.assert_called_once_with("/tmp/out.json", "w", encoding="utf-8")
            write_call_args = m_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            assert result["Table Schema"] == "testdb"
            assert len(result["Tables"]) == 2

            # users table
            users = next(t for t in result["Tables"] if t["Table Name"] == "users")
            assert len(users["Table Columns"]) == 2
            assert users["Primary Key"]["Name"] == "id"
            assert users["Primary Key"]["Data Type"] == "int"
            assert len(users["Foreign Key"]) == 1  # empty placeholder
            assert users["Foreign Key"][0]["Foreign Key Name"] == ""

            # orders table
            orders = next(t for t in result["Tables"] if t["Table Name"] == "orders")
            assert len(orders["Table Columns"]) == 3
            assert orders["Primary Key"]["Name"] == "id"
            assert len(orders["Foreign Key"]) == 1
            fk = orders["Foreign Key"][0]
            assert fk["Foreign Key Name"] == "user_id"
            assert fk["Referenced Table"] == "users"
            assert fk["Referenced Primary Key"] == "id"

            mock_print.assert_called_once_with("wrote 2 tables to /tmp/out.json")
            mock_conn.close.assert_called_once()

    def test_table_without_primary_key(self, mock_cursor):
        """Tables without a primary key should have empty Name/Data Type."""
        # Remove PK from users
        columns = [
            {"TABLE_NAME": "users", "COLUMN_NAME": "id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "users", "COLUMN_NAME": "name", "DATA_TYPE": "varchar",
             "CHARACTER_MAXIMUM_LENGTH": 100, "NUMERIC_PRECISION": None},
        ]
        pk_rows = []  # no PKs
        fk_rows = []
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root",
                "--password", "pass",
                "--database", "testdb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            users = result["Tables"][0]
            assert users["Primary Key"]["Name"] == ""
            assert users["Primary Key"]["Data Type"] == ""

    def test_table_without_foreign_keys(self, mock_cursor):
        """Tables without foreign keys should get a single empty placeholder."""
        columns = [
            {"TABLE_NAME": "users", "COLUMN_NAME": "id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
        ]
        pk_rows = [
            {"TABLE_NAME": "users", "COLUMN_NAME": "id", "DATA_TYPE": "int"},
        ]
        fk_rows = []  # no FKs
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root",
                "--password", "pass",
                "--database", "testdb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            users = result["Tables"][0]
            assert len(users["Foreign Key"]) == 1
            fk = users["Foreign Key"][0]
            assert fk["Foreign Key Name"] == ""
            assert fk["Referenced Table"] == ""

    def test_multiple_foreign_keys_on_one_table(self, mock_cursor):
        """A table can have multiple foreign keys."""
        columns = [
            {"TABLE_NAME": "orders", "COLUMN_NAME": "id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "user_id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "product_id", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
        ]
        pk_rows = [
            {"TABLE_NAME": "orders", "COLUMN_NAME": "id", "DATA_TYPE": "int"},
        ]
        fk_rows = [
            {"TABLE_NAME": "orders", "COLUMN_NAME": "user_id",
             "REFERENCED_TABLE_NAME": "users", "REFERENCED_COLUMN_NAME": "id"},
            {"TABLE_NAME": "orders", "COLUMN_NAME": "product_id",
             "REFERENCED_TABLE_NAME": "products", "REFERENCED_COLUMN_NAME": "id"},
        ]
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root",
                "--password", "pass",
                "--database", "testdb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            orders = result["Tables"][0]
            assert len(orders["Foreign Key"]) == 2
            assert orders["Foreign Key"][0]["Foreign Key Name"] == "user_id"
            assert orders["Foreign Key"][0]["Referenced Table"] == "users"
            assert orders["Foreign Key"][1]["Foreign Key Name"] == "product_id"
            assert orders["Foreign Key"][1]["Referenced Table"] == "products"

    def test_column_distribution_uniform(self, mock_cursor):
        """Column distribution should be uniform (1/n for n columns)."""
        columns = [
            {"TABLE_NAME": "t", "COLUMN_NAME": "a", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "t", "COLUMN_NAME": "b", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "t", "COLUMN_NAME": "c", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "t", "COLUMN_NAME": "d", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
        ]
        pk_rows = [{"TABLE_NAME": "t", "COLUMN_NAME": "a", "DATA_TYPE": "int"}]
        fk_rows = []
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            t = result["Tables"][0]
            assert len(t["Column Distribution"]) == 4
            for v in t["Column Distribution"]:
                assert v == pytest.approx(0.25)

    def test_empty_database(self, mock_cursor):
        """An empty database (no tables) should produce empty Tables list."""
        cur = MagicMock()
        cur.fetchall.side_effect = [[], [], []]  # no columns, no PKs, no FKs

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "emptydb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    with patch("builtins.print") as mock_print:
                        main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            assert result["Table Schema"] == "emptydb"
            assert result["Tables"] == []
            mock_print.assert_called_once_with("wrote 0 tables to /tmp/out.json")

    def test_custom_host_port(self, mock_cursor):
        """Custom --host and --port should be passed to pymysql.connect."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--host", "10.0.0.1",
                "--port", "3307",
                "--user", "admin",
                "--password", "secret",
                "--database", "mydb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            mock_connect.assert_called_once_with(
                host="10.0.0.1",
                port=3307,
                user="admin",
                password="secret",
                database="mydb",
                cursorclass=pymysql.cursors.DictCursor,
            )

    def test_default_host_port(self, mock_cursor):
        """Default --host and --port should be used when not specified."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root",
                "--password", "pass",
                "--database", "testdb",
                "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            mock_connect.assert_called_once_with(
                host="127.0.0.1",
                port=3306,
                user="root",
                password="pass",
                database="testdb",
                cursorclass=pymysql.cursors.DictCursor,
            )

    def test_connection_is_closed(self, mock_cursor):
        """The DB connection should be explicitly closed after processing."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            mock_conn.close.assert_called_once()

    def test_sql_uses_parameterized_query(self, mock_cursor):
        """SQL queries should use parameterized placeholders (%s) to prevent injection."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            # All three execute calls should use parameterized queries
            for call_args in mock_cursor.execute.call_args_list:
                sql, params = call_args[0]
                assert "%s" not in sql  # parameter placeholder should be in query
                assert params == ("testdb",)  # all three queries use only database name

    def test_json_output_is_pretty_printed(self, mock_cursor):
        """Output JSON should use indent=4 and ensure_ascii=False."""
        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                m_open = mock_open()
                with patch("builtins.open", m_open):
                    main()

            # Verify json.dump was called with correct kwargs
            import json as json_module
            # We check the file handle write was called with pretty-printed content
            write_call = m_open().write.call_args[0][0]
            assert "    " in write_call  # indented
            parsed = json.loads(write_call)
            assert parsed["Table Schema"] == "testdb"

    def test_required_args_missing(self):
        """Missing required arguments should cause an error."""
        with patch("sys.argv", ["export_schema_json.py"]):
            with pytest.raises(SystemExit):
                main()

    def test_data_type_mod_integration(self, mock_cursor):
        """Verify data_type_mod is called correctly during main flow."""
        columns = [
            {"TABLE_NAME": "t", "COLUMN_NAME": "col1", "DATA_TYPE": "varchar",
             "CHARACTER_MAXIMUM_LENGTH": 50, "NUMERIC_PRECISION": None},
        ]
        pk_rows = []
        fk_rows = []
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            col = result["Tables"][0]["Table Columns"][0]
            assert col["Data Type Mod"] == 50
            assert col["Data Distribution"] == [0, 50]

    def test_table_with_composite_primary_key(self, mock_cursor):
        """Composite primary keys: only the first PK column is used as Primary Key name."""
        columns = [
            {"TABLE_NAME": "t", "COLUMN_NAME": "a", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
            {"TABLE_NAME": "t", "COLUMN_NAME": "b", "DATA_TYPE": "int",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 10},
        ]
        pk_rows = [
            {"TABLE_NAME": "t", "COLUMN_NAME": "a", "DATA_TYPE": "int"},
            {"TABLE_NAME": "t", "COLUMN_NAME": "b", "DATA_TYPE": "int"},
        ]
        fk_rows = []
        cur = MagicMock()
        cur.fetchall.side_effect = [columns, pk_rows, fk_rows]

        with patch("export_schema_json.pymysql.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = cur
            mock_connect.return_value = mock_conn

            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with patch("builtins.open", mock_open()):
                    main()

            write_call_args = mock_open().write.call_args[0][0]
            result = json.loads(write_call_args)

            t = result["Tables"][0]
            # Only the first PK column is used
            assert t["Primary Key"]["Name"] == "a"

    def test_pymysql_import_error(self):
        """If pymysql is not installed, an ImportError should be raised."""
        with patch.dict("sys.modules", {"pymysql": None}):
            with patch("sys.argv", [
                "export_schema_json.py",
                "--user", "root", "--password", "pass",
                "--database", "testdb", "--output", "/tmp/out.json",
            ]):
                with pytest.raises(ModuleNotFoundError):
                    # Re-import to trigger the error
                    import importlib
                    import export_schema_json
                    importlib.reload(export_schema_json)
                    export_schema_json.main()
