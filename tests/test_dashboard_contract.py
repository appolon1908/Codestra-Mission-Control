from mission_control.dashboard_contract import dashboard_contract


def test_every_dashboard_surface_has_api_contract():
    c = dashboard_contract()["endpoints"]
    required = {
        "repositories",
        "sources",
        "repository",
        "agents",
        "notifications",
        "assignment_claim",
        "router_snapshot",
        "router_next",
        "watchdog_snapshot",
        "worker_nodes",
    }
    assert required <= set(c)
    assert all(
        v["method"] in {"GET", "POST", "PUT", "DELETE"}
        and v["path"].startswith("/platform/v1/")
        and v["ui"]
        for v in c.values()
    )
