import math
from typing import Tuple

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on the Earth
    specified in decimal degrees (latitude and longitude).
    Returns distance in meters.
    """
    try:
        lat1 = float(lat1 or 0)
        lon1 = float(lon1 or 0)
        lat2 = float(lat2 or 0)
        lon2 = float(lon2 or 0)
    except (ValueError, TypeError):
        return 999999.0

    if lat1 == 0 and lon1 == 0:
        return 999999.0

    R = 6371000.0  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    distance = R * c
    return round(distance, 2)


def evaluate_geofence(
    user_lat: float,
    user_lon: float,
    target_lat: float,
    target_lon: float,
    radius_limit: float = 50.0,
    accuracy: float = 0.0
) -> Tuple[bool, float, float, str]:
    """
    Smart geofence evaluator that accounts for GPS accuracy uncertainty,
    Wi-Fi/IP location drift, and kindergarten perimeter tolerance.

    Returns:
        (is_within, raw_distance, effective_distance, message)
    """
    try:
        u_lat = float(user_lat or 0)
        u_lon = float(user_lon or 0)
        t_lat = float(target_lat or 0)
        t_lon = float(target_lon or 0)
        radius = float(radius_limit or 50.0)
        acc = max(0.0, float(accuracy or 0.0))
    except (ValueError, TypeError):
        return False, 999999.0, 999999.0, "GPS koordinatalari formati xato."

    if u_lat == 0 and u_lon == 0:
        return False, 999999.0, 999999.0, "GPS koordinatalari olinmadi."

    raw_dist = haversine_distance(u_lat, u_lon, t_lat, t_lon)

    # 1. Exact GPS match within base radius
    if raw_dist <= radius:
        return True, raw_dist, raw_dist, f"Hudud ichida (Masofa: {raw_dist:.1f}m <= {radius:.0f}m)"

    # 2. Yard / Campus perimeter soft tolerance (kindergarten campus is typically 80-120m wide)
    yard_tolerance = max(radius * 2.0, 100.0)
    if raw_dist <= yard_tolerance:
        return True, raw_dist, raw_dist, f"Bog'cha perimetrida (Masofa: {raw_dist:.1f}m <= {yard_tolerance:.0f}m)"

    # 3. Accuracy uncertainty buffer (Wi-Fi / Cell / IP triangulation drift)
    # If device reports accuracy of ±500m or ±1000m, effective minimum distance is raw_dist - accuracy.
    # Cap accuracy buffer at 1500m to avoid accepting completely different cities/districts.
    effective_acc = min(acc, 1500.0)
    effective_dist = max(0.0, round(raw_dist - effective_acc, 2))

    if effective_dist <= radius:
        return True, raw_dist, effective_dist, (
            f"Hudud ichida (GPS noaniqligi hisobga olindi: Masofa {raw_dist:.1f}m, "
            f"Aniqlik ±{acc:.0f}m, Samarali masofa: {effective_dist:.1f}m <= {radius:.0f}m)"
        )

    # 4. Out of geofence
    return False, raw_dist, effective_dist, (
        f"Bog'cha hududidan {raw_dist:.1f} metr uzoqdasiz. "
        f"(Ruxsat: {radius:.0f}m, GPS aniqligi: ±{acc:.0f}m)"
    )

