"""Voxel hexahedral FEM modal analysis.

Every element is the same unit cube, so a single 24x24 stiffness matrix (trilinear
hex + Wilson incompatible modes, statically condensed to suppress bending locking)
is reused for the whole object. The solve runs in a unit system (E = 1, rho = 1,
voxel h = 1); physical frequencies follow from

    lambda_phys = lambda_unit * E / (rho * h^2),   f = sqrt(lambda_phys) / 2pi

which is exactly why resizing (h) and swapping material (E / rho) never need a
re-solve: one FEM run per *shape*.
"""
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree

# One Poisson ratio for every material, so a material swap keeps the mode shapes.
NU = 0.3
# Local node order of a voxel: x fastest, then y, then z.
CORNERS = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)])
N_RIGID = 6


def element_stiffness(nu=NU):
    """24x24 stiffness of a unit cube (E = 1) with condensed incompatible modes."""
    lam = nu / ((1 + nu) * (1 - 2 * nu))
    mu = 1 / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[range(3), range(3)] += 2 * mu
    D[3:, 3:] = np.eye(3) * mu

    signs = 2 * CORNERS - 1
    g = 1 / np.sqrt(3)
    K = np.zeros((33, 33))
    for p in np.array(np.meshgrid([-g, g], [-g, g], [-g, g], indexing="ij")).reshape(3, -1).T:
        # d/dx = 2 d/dxi for a unit cube; detJ = 1/8, Gauss weights are 1.
        grads = []
        for s in signs:
            f = 1 + s * p
            grads.append(np.array([s[0] * f[1] * f[2], f[0] * s[1] * f[2], f[0] * f[1] * s[2]]) / 4)
        for d in range(3):  # incompatible bubbles 1 - xi_d^2
            gvec = np.zeros(3)
            gvec[d] = -4 * p[d]
            grads.append(gvec)
        B = np.zeros((6, 33))
        for a, (gx, gy, gz) in enumerate(grads):
            c = 3 * a
            B[0, c], B[1, c + 1], B[2, c + 2] = gx, gy, gz
            B[3, c + 1], B[3, c + 2] = gz, gy
            B[4, c], B[4, c + 2] = gz, gx
            B[5, c], B[5, c + 1] = gy, gx
        K += B.T @ D @ B / 8
    Kaa, Kab, Kbb = K[:24, :24], K[:24, 24:], K[24:, 24:]
    return Kaa - Kab @ np.linalg.solve(Kbb, Kab.T)


def largest_component(occ):
    """Keep the largest face-connected blob; edge/corner contacts make fake hinges."""
    labels, n = ndimage.label(occ)
    if n <= 1:
        return occ
    sizes = np.bincount(labels.ravel())[1:]
    return labels == (np.argmax(sizes) + 1)


def node_lattice(occ):
    shape = np.array(occ.shape) + 1
    vox = np.argwhere(occ)
    corners = (vox[:, None, :] + CORNERS[None]).reshape(-1, 3)
    lin = np.ravel_multi_index(corners.T, shape).reshape(-1, 8)
    used, inv = np.unique(lin, return_inverse=True)
    node_xyz = np.array(np.unravel_index(used, shape)).T.astype(np.float64)
    return inv.reshape(-1, 8), node_xyz


def _spd_solver(A):
    """Solve callable for the SPD shift matrix K + |sigma| M.

    CHOLMOD with METIS ordering does a 180k-voxel bowl in about a minute; SuperLU
    with a symmetric ordering is the fallback (~11x faster than eigsh's default
    COLAMD, yet still minutes at that size).
    """
    try:
        from sksparse.cholmod import cho_factor
    except ImportError:
        lu = spla.splu(A, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                       options={"SymmetricMode": True})
        return lu.solve
    return cho_factor(A, order="metis").solve


def solve_modes(occ, n_modes=40, nu=NU):
    """Return (lambda_unit (m,), phi (n_nodes, 3, m), node_xyz (n_nodes, 3)).

    occ may be boolean or a fill fraction in [0, 1]; partially filled boundary
    voxels get proportionally less stiffness and mass, which keeps thin walls
    from snapping to a whole number of voxels.
    """
    Ke = element_stiffness(nu)
    occ = np.asarray(occ, dtype=float)
    elem_nodes, node_xyz = node_lattice(occ > 0)
    frac = occ[occ > 0]  # same C order as np.argwhere in node_lattice
    n = len(node_xyz)
    dofs = (3 * elem_nodes[:, :, None] + np.arange(3)).reshape(-1, 24).astype(np.int32)
    rows = np.repeat(dofs, 24, axis=1).ravel()
    cols = np.tile(dofs, (1, 24)).ravel()
    data = (frac[:, None] * Ke.ravel()[None, :]).ravel()
    K = sp.csc_matrix((data, (rows, cols)), shape=(3 * n, 3 * n))
    del rows, cols, data
    node_mass = np.bincount(elem_nodes.ravel(), weights=np.repeat(frac, 8), minlength=n) / 8.0  # lumped
    M = sp.diags(np.repeat(node_mass, 3)).tocsc()

    sigma = -1e-6
    op_inv = spla.LinearOperator(K.shape, matvec=_spd_solver((K - sigma * M).tocsc()), dtype=float)
    vals, vecs = spla.eigsh(K, k=n_modes + N_RIGID, M=M, sigma=sigma, which="LM", OPinv=op_inv)
    order = np.argsort(vals)
    vals, vecs = vals[order], vecs[:, order]
    if vals[N_RIGID] < 1e3 * max(abs(vals[N_RIGID - 1]), 1e-12):
        raise RuntimeError(f"rigid/elastic modes not separated: {vals[:N_RIGID + 2]}")
    vals, vecs = vals[N_RIGID:], vecs[:, N_RIGID:]
    return vals, vecs.reshape(n, 3, -1), node_xyz


def inside_fraction(mesh, centre0, dims, pitch, sub=2):
    """Per-voxel inside fraction on a given grid (voxel (0,0,0) centred at centre0).

    One +z ray per sub-sample column with even-odd parity: far cheaper than
    per-point containment. `mesh` must be watertight.
    """
    lo = np.asarray(centre0) - pitch / 2
    s = (np.arange(sub) + 0.5) / sub  # sub-sample offsets inside a voxel
    axes = [lo[a] + (np.arange(dims[a])[:, None] + s[None]).ravel() * pitch for a in range(3)]
    gx, gy = np.meshgrid(axes[0], axes[1], indexing="ij")
    rng = np.random.default_rng(0)  # tiny jitter keeps rays off shared edges
    origins = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, lo[2] - 1.0)], axis=1)
    origins[:, :2] += rng.uniform(-1e-7, 1e-7, (gx.size, 2))
    dirs = np.tile([0.0, 0.0, 1.0], (gx.size, 1))
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=True)

    inside = np.zeros((gx.size, axes[2].size), bool)
    order = np.lexsort((locs[:, 2], ray_idx))
    ray_idx, hz = ray_idx[order], locs[order, 2]
    starts = np.searchsorted(ray_idx, np.arange(gx.size))
    ends = np.searchsorted(ray_idx, np.arange(gx.size), side="right")
    for r in np.nonzero(ends > starts)[0]:
        crossings = np.searchsorted(hz[starts[r]:ends[r]], axes[2])
        inside[r] = crossings % 2 == 1
    return inside.reshape(dims[0], sub, dims[1], sub, dims[2], sub).mean(axis=(1, 3, 5))


def voxelize(mesh_norm, n_max, sub=2):
    """Fill fraction per voxel from sub^3 inside-samples, plus voxel (0,0,0) centre.

    Inside tests use one +z ray per sample column and even-odd parity, which is
    far cheaper than per-point containment. Non-watertight meshes fall back to
    surface + hole-fill voxelisation, where parity would be meaningless.
    """
    pitch = 1.0 / n_max
    # GLBs split vertices along UV seams; weld a copy so seams don't read as holes.
    mesh_norm = mesh_norm.copy()
    mesh_norm.merge_vertices(merge_tex=True, merge_norm=True)
    if not mesh_norm.is_watertight:
        vg = mesh_norm.voxelized(pitch).fill()
        return largest_component(vg.matrix).astype(float), vg.transform[:3, 3].copy()

    lo = mesh_norm.bounds[0] - pitch
    dims = np.ceil((mesh_norm.bounds[1] + pitch - lo) / pitch).astype(int)
    frac = inside_fraction(mesh_norm, lo + pitch / 2, dims, pitch, sub)
    return np.where(largest_component(frac > 0), frac, 0.0), lo + pitch / 2


def shell_voxelize(mesh_norm, n_max, wall):
    """Surface thickened to `wall` (normalised units), plus any enclosed interior.

    Generated meshes carry no physical wall thickness (a Lux3D bowl came back with
    0.5 mm walls), so pitch and printability need an explicit wall.
    """
    pitch = 1.0 / n_max
    welded = mesh_norm.copy()
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    vg = welded.voxelized(pitch)  # surface voxels only
    pad = int(np.ceil(wall / pitch)) + 2
    surf = np.pad(vg.matrix, pad)
    origin = vg.transform[:3, 3] - pad * pitch
    # Whole-voxel bands quantise the wall (a 3 mm strip came out 0.44-1.55x thick
    # depending on resolution), so fill partially by true distance to the surface:
    # frac = clip((wall/2 - d) / pitch + 1/2, 0, 1) gives the right mean thickness.
    near = ndimage.distance_transform_edt(~surf) * pitch <= wall / 2 + 1.5 * pitch
    idx = np.argwhere(near)
    centres = origin + idx * pitch
    n_samples = int(min(4e6, max(2e5, welded.area / (pitch / 4) ** 2)))
    samples, _ = trimesh.sample.sample_surface(welded, n_samples, seed=0)
    d, _ = cKDTree(samples).query(centres)
    frac = np.zeros(surf.shape)
    frac[tuple(idx.T)] = np.clip((wall / 2 - d) / pitch + 0.5, 0, 1)
    if welded.is_watertight:
        # Only the mesh's own interior: hole-filling also plugged enclosed air (a
        # Lux3D bowl's foot cavity under its floor), which doubled its pitch.
        frac = np.maximum(frac, inside_fraction(welded, origin, surf.shape, pitch))
    return np.where(largest_component(frac > 0), frac, 0.0), origin


def auto_resolution(mesh_norm, walls=None, n0=64, budget=180000, n_min=48, n_max=260, wall=None, log=print):
    """Grid size that puts ~`walls` voxels across the thin walls, within a voxel budget.

    The bar convergence study (scripts/validate_bar.py, notes in docs) showed
    bending error of +20% at 2.3 voxels through the thickness and <1% at 4.5.
    Wall thickness is `wall` when given, else read off the distance-transform
    ridge of a coarse grid. Thickened shells (fractional fill by surface distance)
    reach +1% at 3.1 voxels through the wall vs +15% at 2.4 and +26-43% below 2
    (zero-thickness strip study, 2026-10-31), hence 3.2 for them.
    """
    walls = walls or (3.2 if wall is not None else 4.5)
    if wall is not None:
        # Shell voxel counts grow like a N^3 (wall volume) + b N^2 (partial surface
        # layers); fit both from two coarse grids instead of guessing the split.
        n1 = int(n0 * 1.5)
        c0, c1 = ((shell_voxelize(mesh_norm, n, wall)[0] > 0).sum() for n in (n0, n1))
        a, b = np.linalg.solve([[n0 ** 3, n0 ** 2], [n1 ** 3, n1 ** 2]], [c0, c1])
        thickness = wall * n0
        grid = np.arange(n_min, n_max + 1)
        fits = grid[a * grid ** 3 + b * grid ** 2 <= budget]
        cap = fits.max() if len(fits) else n_min
    else:
        occ, _ = voxelize(mesh_norm, n0)
        solid = occ > 0.5
        edt = ndimage.distance_transform_edt(solid)
        ridge = solid & (edt >= ndimage.maximum_filter(edt, size=3))
        thickness = 2 * np.percentile(edt[ridge], 20) if ridge.any() else 2.0  # in voxels at n0
        cap = n0 * (budget / max(solid.sum(), 1)) ** (1 / 3)
    want = n0 * walls / max(thickness, 0.5)
    n = int(np.clip(min(want, cap), n_min, n_max))
    if want > cap:
        log(f"  resolution capped at N={n} by the {budget}-voxel budget "
            f"(~{thickness * n / n0:.1f} voxels across thin walls; expect higher pitch error)")
    return n


def shell_overstiffness(voxels_across):
    """Relative pitch error of thickened shells vs voxels through the wall.

    From a zero-thickness 3 mm aluminium strip against Euler-Bernoulli (2026-10-31):
    +31.7% at 1.92, +15.0% at 2.40, +1.1% at 3.12. Bending-only and noisy below 2,
    so treat corrected pitches as +-10%.
    """
    return float(np.interp(voxels_across, [1.92, 2.40, 3.12, 4.0], [0.317, 0.150, 0.011, 0.0]))


def normalize_mesh(mesh):
    """Copy of mesh translated to the origin corner and scaled to max extent 1."""
    m = mesh.copy()
    m.apply_translation(-m.bounds[0])
    m.apply_scale(1.0 / m.extents.max())
    return m


@dataclass
class ModalModel:
    lam_unit: np.ndarray  # (m,) eigenvalues for E = 1, rho = 1, h = 1
    phi: np.ndarray       # (n_nodes, 3, m) mass-normalised in the unit system
    node_xyz: np.ndarray  # (n_nodes, 3) node lattice coordinates
    pitch: float          # voxel size in normalised-mesh units (mesh max extent = 1)
    origin: np.ndarray    # normalised-mesh position of voxel (0, 0, 0)'s centre
    n_voxels: int
    _tree: cKDTree = field(default=None, repr=False)

    @classmethod
    def from_mesh(cls, mesh_norm, n_max=None, n_modes=40, wall=None, budget=180000, log=print):
        """mesh_norm must come from normalize_mesh (max extent 1).

        n_max=None picks the resolution automatically (see auto_resolution).
        wall (normalised units) thickens the surface to a physical wall first; when
        the budget leaves fewer than 3.2 voxels across it, eigenvalues get the rough
        strip-study correction from shell_overstiffness.
        """
        if n_max is None:
            n_max = auto_resolution(mesh_norm, wall=wall, budget=budget, log=log)
        if wall is None:
            occ, origin = voxelize(mesh_norm, n_max)
        else:
            occ, origin = shell_voxelize(mesh_norm, n_max, wall)
        lam, phi, node_xyz = solve_modes(occ, n_modes)
        if wall is not None:
            e = shell_overstiffness(wall * n_max)
            if e > 0.005:
                lam = lam / (1 + e) ** 2  # f ∝ sqrt(lambda)
                log(f"  thin-wall correction: pitch / {1 + e:.3f} ({wall * n_max:.1f} voxels across the wall; rough)")
        return cls(lam, phi, node_xyz, 1.0 / n_max, origin, int((occ > 0).sum()))

    def frequencies(self, E, rho, size_m):
        """Modal frequencies (Hz) for a material and a physical max extent in metres."""
        h = size_m * self.pitch
        return np.sqrt(np.maximum(self.lam_unit, 0) * E / rho) / (2 * np.pi * h)

    def sample(self, points):
        """Interpolate mode shapes at normalised-mesh points -> (P, 3, m)."""
        if self._tree is None:
            self._tree = cKDTree(self.node_xyz)
        # Node (i, j, k) sits at voxel-index position (i, j, k) - 0.5.
        q = (np.asarray(points) - self.origin) / self.pitch + 0.5
        d, idx = self._tree.query(q, k=8)
        w = 1.0 / (d + 0.05) ** 2
        w /= w.sum(axis=1, keepdims=True)
        return np.einsum("pk,pkcm->pcm", w, self.phi[idx])
