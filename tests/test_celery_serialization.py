"""Tests for Celery JSON serialization helpers (to_json_transport / from_json_transport)."""

import os

import numpy as np
import pandas as pd
import pytest

from quantgpt.celery_app import CELERY_DATA_DIR, from_json_transport, to_json_transport


class TestToJsonTransport:
    def test_primitives_unchanged(self):
        assert to_json_transport(42) == 42
        assert to_json_transport("hello") == "hello"
        assert to_json_transport(True) is True
        assert to_json_transport(None) is None

    def test_nan_becomes_none(self):
        assert to_json_transport(float("nan")) is None
        assert to_json_transport(np.float64("nan")) is None

    def test_inf_becomes_none(self):
        assert to_json_transport(float("inf")) is None
        assert to_json_transport(float("-inf")) is None

    def test_numpy_int(self):
        result = to_json_transport(np.int64(42))
        assert result == 42
        assert type(result) is int

    def test_numpy_float(self):
        result = to_json_transport(np.float64(3.14))
        assert result == pytest.approx(3.14)
        assert type(result) is float

    def test_numpy_bool(self):
        result = to_json_transport(np.bool_(True))
        assert result is True

    def test_numpy_array(self):
        arr = np.array([1, 2, 3])
        assert to_json_transport(arr) == [1, 2, 3]

    def test_set_becomes_list(self):
        result = to_json_transport({1, 2, 3})
        assert sorted(result) == [1, 2, 3]

    def test_nested_dict(self):
        data = {"a": np.int64(1), "b": [np.float64(2.0), float("nan")]}
        result = to_json_transport(data)
        assert result == {"a": 1, "b": [2.0, None]}

    def test_dataframe_creates_parquet(self):
        df = pd.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]})
        result = to_json_transport(df)
        assert "__quantgpt_parquet__" in result
        assert result["__type__"] == "dataframe"
        assert os.path.exists(result["__quantgpt_parquet__"])
        os.unlink(result["__quantgpt_parquet__"])

    def test_series_creates_parquet(self):
        s = pd.Series([10, 20, 30], name="vals")
        result = to_json_transport(s)
        assert "__quantgpt_parquet__" in result
        assert result["__type__"] == "series"
        assert os.path.exists(result["__quantgpt_parquet__"])
        os.unlink(result["__quantgpt_parquet__"])


class TestRoundTrip:
    def test_dataframe_roundtrip(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
        transported = to_json_transport(df)
        restored = from_json_transport(transported)
        assert isinstance(restored, pd.DataFrame)
        pd.testing.assert_frame_equal(restored, df)

    def test_series_roundtrip(self):
        s = pd.Series([10.0, 20.0, 30.0], name="vals")
        transported = to_json_transport(s)
        restored = from_json_transport(transported)
        assert isinstance(restored, pd.Series)
        pd.testing.assert_series_equal(restored, s, check_names=False)

    def test_nested_dict_with_dataframes(self):
        df = pd.DataFrame({"x": [1, 2]})
        s = pd.Series([3.0, 4.0])
        data = {
            "result": df,
            "factor": s,
            "score": 42,
            "metrics": {"ic": 0.05, "ir": np.float64(1.2)},
        }
        transported = to_json_transport(data)
        restored = from_json_transport(transported)

        assert isinstance(restored["result"], pd.DataFrame)
        pd.testing.assert_frame_equal(restored["result"], df)
        assert isinstance(restored["factor"], pd.Series)
        assert restored["score"] == 42
        assert restored["metrics"]["ic"] == 0.05
        assert restored["metrics"]["ir"] == pytest.approx(1.2)

    def test_args_list_roundtrip(self):
        df = pd.DataFrame({"col": [1, 2, 3]})
        args = [df, "rank(close)", 5, 10]
        transported = to_json_transport(args)
        restored = from_json_transport(transported)

        assert isinstance(restored[0], pd.DataFrame)
        pd.testing.assert_frame_equal(restored[0], df)
        assert restored[1] == "rank(close)"
        assert restored[2] == 5
        assert restored[3] == 10

    def test_parquet_files_cleaned_up(self):
        df = pd.DataFrame({"x": [1]})
        transported = to_json_transport(df)
        path = transported["__quantgpt_parquet__"]
        assert os.path.exists(path)

        from_json_transport(transported)
        assert not os.path.exists(path)


class TestPathTraversal:
    def test_rejects_path_outside_data_dir(self):
        marker = {
            "__quantgpt_parquet__": "/etc/passwd",
            "__type__": "dataframe",
        }
        with pytest.raises(ValueError, match="outside allowed directory"):
            from_json_transport(marker)

    def test_rejects_relative_traversal(self):
        marker = {
            "__quantgpt_parquet__": str(CELERY_DATA_DIR / ".." / ".." / "etc" / "passwd"),
            "__type__": "dataframe",
        }
        with pytest.raises(ValueError, match="outside allowed directory"):
            from_json_transport(marker)
