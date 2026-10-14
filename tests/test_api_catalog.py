from mission_control.api_catalog import APICatalog


def test_catalog_has_reusable_control_plane_surfaces():
    c = APICatalog()
    paths = c.document()["paths"]
    for p in (
        "/platform/v1/services",
        "/platform/v1/connectors",
        "/platform/v1/events",
        "/platform/v1/assignments",
        "/platform/v1/tasks/{task_id}/evidence",
    ):
        assert p in paths


def test_effectful_endpoints_are_explicitly_classified():
    for e in APICatalog().all():
        assert e["effect"] in {"READ", "CONTROL"}
