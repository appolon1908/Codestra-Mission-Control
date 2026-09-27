from mission_control.service_catalog import ServiceCatalog

def test_realtime_gateway_has_dedicated_noncolliding_endpoint():
 s=ServiceCatalog().get("realtime-gateway")
 assert s["health"]=="http://127.0.0.1:8791/healthz"
 assert s["base_url"]=="ws://127.0.0.1:8791"

def test_agent_brain_declares_realtime_dependency():
 assert "realtime-gateway" in ServiceCatalog().get("agent-brain")["dependencies"]
