import sys


def test_model_module_imports_without_torch():
    # Importing the module must not pull torch into the process.
    import norn_forecast.timesfm_model as m

    assert hasattr(m, "TimesFM25Model")
    assert "torch" not in sys.modules
