import pytest


def test_functions_entry_point_loads():
    pytest.importorskip("azure.functions")
    import function_app

    assert len(function_app.app.get_functions()) == 1
