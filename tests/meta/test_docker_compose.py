from pathlib import Path


ROOT = Path(__file__).parents[2]
DOCKER_COMPOSE = ROOT / "docker-compose.yml"
PODMAN_COMPOSE = ROOT / "docker-compose.podman.yml"


def _services(path):
    services = {}
    service = None
    in_services = False

    for line in path.read_text().splitlines():
        if line == "services:":
            in_services = True
        elif in_services and line and not line.startswith((" ", "#")):
            break
        elif in_services and len(line) - len(line.lstrip()) == 4 and line.rstrip().endswith(":"):
            service = line.strip()[:-1]
            services[service] = set()
        elif in_services and service is not None and len(line) - len(line.lstrip()) > 4 and ":" in line:
            services[service].add(line.strip().split(":", 1)[0])

    return services


def test_podman_compose_inherits_every_required_service_image():
    docker_services = _services(DOCKER_COMPOSE)
    podman_services = _services(PODMAN_COMPOSE)

    assert all("image" in fields for fields in docker_services.values())
    assert podman_services.keys() <= docker_services.keys()

    services_requiring_host_network = {
        service for service, fields in docker_services.items() if "ports" in fields and "network_mode" not in fields
    }

    assert services_requiring_host_network <= podman_services.keys()
    assert all("image" not in fields for fields in podman_services.values())
