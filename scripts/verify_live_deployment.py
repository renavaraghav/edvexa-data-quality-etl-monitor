"""Read-only deployment smoke checks."""
import argparse
import httpx


def run_live_verification(base_url: str) -> bool:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        if health.json().get("status") != "healthy":
            raise RuntimeError("Deployment health is degraded")
        for path in ("/", "/api/dashboard/summary", "/api/dashboard/reset-status"):
            client.get(path).raise_for_status()
    print("Read-only deployment smoke checks passed.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    run_live_verification(parser.parse_args().base_url)
