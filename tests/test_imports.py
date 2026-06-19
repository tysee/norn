def test_packages_import():
    import norn_core
    import norn_cli  # noqa: F401
    import norn_forecast  # noqa: F401
    import norn_integration  # noqa: F401

    assert norn_core.__version__ == "0.0.0"
