def test_package_is_importable() -> None:
    import reference_engine

    assert reference_engine.__name__ == "reference_engine"
