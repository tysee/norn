def test_packages_import():
    import norn_core
    import norn_integration
    import norn_forecast
    import norn_cli

    assert norn_core.__version__ == "0.0.0"
