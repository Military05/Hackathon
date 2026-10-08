"""Scenario access follows the incident's target, not the vehicle's label."""

TARGETS = {
    "logistics": ("asset", "V1"), "service": ("asset", "V3"),
    "shift": ("building", "G1"), "unauthorized-access": ("building", "G1"),
    "sensor-offline": ("sensor", "HB-QA"),
    "forbidden-zone": ("zone", "Z1"), "orange-zone": ("zone", "Z1"),
    "orange-authorized": ("zone", "Z1"), "red-zone": ("zone", "Z6"),
    "production-zone": ("zone", "Z5"), "service-zone": ("zone", "Z4"),
    "route-deviation": ("asset", "V1"), "collision": ("asset", "V1"),
    "safe-passing": ("asset", "V1"),
    "anomaly-oscillation": ("asset", "V1"), "anomaly-wall": ("asset", "V1"),
    "anomaly-erratic": ("asset", "V1"),
}


def scenario_sectors(service, scenario):
    if scenario == "normal":
        return sorted({profile["sector_id"] for profile in service.profiles.values()})
    target = TARGETS.get(scenario)
    if not target:
        return []  # Mixed/unknown demonstrations belong to the administrator.
    kind, identifier = target
    if kind == "asset":
        if identifier not in service.assets_by_id:
            return []
        responsibility = service._responsibility(asset_id=identifier)
    elif kind == "building":
        if identifier not in service.buildings:
            return []
        responsibility = service._responsibility(building_id=identifier)
    elif kind == "zone":
        zone = next((row for row in service.site["zones"] if row["id"] == identifier), None)
        if not zone:
            return []
        responsibility = service._responsibility(area_id=zone["site_area_id"])
    else:
        sensor = service.sensors_by_id.get(identifier)
        if not sensor:
            return []
        responsibility = service._responsibility(area_id=sensor.get("site_area_id"),
                                               asset_id=sensor.get("asset_id"), building_id=sensor.get("building_id"))
    return [responsibility["responsible_sector_id"]]


def allowed_scenarios(service, operator, scenarios):
    if operator == "admin":
        return list(scenarios)
    sector = service.profiles[operator]["sector_id"]
    return [scenario for scenario in scenarios if sector in scenario_sectors(service, scenario)]
