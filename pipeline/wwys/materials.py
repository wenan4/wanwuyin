"""Acoustic material table, shared with the web viewer via materials.json.

Density / Young's modulus / Rayleigh damping are starting values in the range used
by modal-sound papers (NeuralSound, ObjectFolder); tune them by ear. Poisson's ratio
is fixed at fem.NU for every material so mode shapes can be reused.
"""
import json

# key: (中文名, rho kg/m^3, E Pa, rayleigh alpha 1/s, rayleigh beta s, look)
MATERIALS = {
    "porcelain": ("瓷", 2400, 7.0e10, 6.0, 1e-7, {"color": "#f3efe6", "metalness": 0.0, "roughness": 0.12}),
    "glass": ("玻璃", 2500, 6.2e10, 1.0, 1e-7, {"color": "#d5ecf2", "metalness": 0.0, "roughness": 0.04, "opacity": 0.55}),
    "bronze": ("青铜", 8800, 1.1e11, 3.0, 4e-8, {"color": "#a8814a", "metalness": 1.0, "roughness": 0.35}),
    "steel": ("钢", 7850, 2.0e11, 5.0, 3e-8, {"color": "#c9cdd2", "metalness": 1.0, "roughness": 0.22}),
    "aluminum": ("铝", 2700, 7.0e10, 5.0, 5e-8, {"color": "#dfe2e6", "metalness": 1.0, "roughness": 0.3}),
    "stone": ("石", 2700, 5.0e10, 20.0, 3e-7, {"color": "#8e8a84", "metalness": 0.0, "roughness": 0.85}),
    "wood": ("木", 750, 1.1e10, 60.0, 2e-6, {"color": "#a26f3c", "metalness": 0.0, "roughness": 0.7}),
    "plastic": ("塑料", 1070, 1.4e9, 30.0, 1e-6, {"color": "#4b8fd6", "metalness": 0.0, "roughness": 0.4}),
    # FDM-printed PLA at 100% infill: E is calibrated per printer with scripts/calibrate_print.py.
    "pla": ("打印 PLA", 1240, 3.0e9, 10.0, 4e-6, {"color": "#e8e2d4", "metalness": 0.0, "roughness": 0.6}),
}


def params(key):
    name, rho, E, alpha, beta, _ = MATERIALS[key]
    return {"name": name, "rho": rho, "E": E, "alpha": alpha, "beta": beta}


def decay_rates(freqs, key):
    """Amplitude decay rate (1/s) per mode from Rayleigh damping."""
    p = params(key)
    w = 2 * 3.141592653589793 * freqs
    return p["alpha"] / 2 + p["beta"] * w ** 2 / 2


def write_json(path):
    out = {}
    for key, (name, rho, E, alpha, beta, look) in MATERIALS.items():
        out[key] = {"name": name, "rho": rho, "E": E, "alpha": alpha, "beta": beta, **look}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
