import numpy as np

# vibration_tools.py
# A library of functions for Mechanics, Machines & Vibrations
# Includes: SDOF, MDOF, mobility, transmission angle, 4-bar linkage, Lagrange, Rayleigh, damping, FRF, and matrix builders.

# ----------------------------
# SINGLE DEGREE OF FREEDOM (SDOF)
# ----------------------------

def sdof_natural_frequency(m: float, k: float) -> float:
    """
    Compute natural frequency (rad/s) of an undamped SDOF: omega_n = sqrt(k/m)
    """
    return np.sqrt(k / m)


def sdof_damping_ratio(c: float, m: float, k: float) -> float:
    """
    Compute damping ratio for viscous damper: zeta = c/(2*sqrt(m*k))
    """
    return c / (2 * np.sqrt(m * k))


def sdof_frf(omega: float, m: float, c: float, k: float) -> complex:
    """
    Frequency Response Function H(omega) = X/F = 1/(k - m*omega^2 + i*c*omega)
    """
    return 1.0 / (k - m * omega**2 + 1j * c * omega)

# ----------------------------
# MULTI DEGREE OF FREEDOM (MDOF)
# ----------------------------

def mdof_natural_modes(M: np.ndarray, K: np.ndarray):
    """
    Compute natural frequencies (rad/s) and mode shapes for undamped MDOF.
    Solves eigenproblem: (K - omega^2 M) phi = 0
    """
    eigvals, eigvecs = np.linalg.eig(np.linalg.inv(M).dot(K))
    omega_n = np.sqrt(np.real(eigvals))
    return omega_n, eigvecs


def rayleigh_damping(M: np.ndarray, K: np.ndarray, zeta1: float, zeta2: float) -> np.ndarray:
    """
    Compute Rayleigh damping matrix C = alpha*M + beta*K
    for desired damping ratios zeta1,zeta2 at two modes.
    """
    omega, _ = mdof_natural_modes(M, K)
    w1, w2 = omega[0], omega[1]
    A = np.array([[1/(2*w1), w1/2], [1/(2*w2), w2/2]])
    alpha, beta = np.linalg.solve(A, [zeta1, zeta2])
    return alpha * M + beta * K

# ----------------------------
# FOUR-BAR LINKAGE KINEMATICS
# ----------------------------

def fourbar_positions(l1: float, l2: float, l3: float, l4: float, theta2: float):
    """
    Compute 4-bar linkage coupler joint positions and output angle theta3.
    Returns ((xA,yA), theta3).
    """
    xA = l2 * np.cos(theta2)
    yA = l2 * np.sin(theta2)
    d = np.hypot(xA - l1, yA)
    cos_phi = (l3**2 + d**2 - l4**2) / (2 * l3 * d)
    phi = np.arccos(np.clip(cos_phi, -1, 1))
    theta3 = np.arctan2(yA, xA - l1) - phi
    return (xA, yA), theta3


def transmission_angle(l1: float, l2: float, l3: float, l4: float, theta2: float) -> float:
    """
    Compute transmission angle gamma between coupler and follower links.
    """
    _, theta3 = fourbar_positions(l1, l2, l3, l4, theta2)
    # gamma = angle between coupler (link2-link3) and follower (link3-ground)
    # approximate: gamma = theta3 - theta2
    return abs(theta3 - theta2)

# ----------------------------
# MOBILITY (Gruebler's Equation)
# ----------------------------

def mobility_2d(joints: int, links: int, lower_pairs: int = None) -> int:
    """
    Compute mobility of a planar mechanism using Gruebler's eq: M = 3(N-1)-2J -H
    where N = joints, J = lower pairs, H = higher pairs (default 0).
    """
    if lower_pairs is None:
        lower_pairs = links
    return 3 * (joints - 1) - 2 * lower_pairs

# ----------------------------
# LAGRANGE & MATRIX BUILDERS
# ----------------------------

def build_mass_matrix(inertias: list) -> np.ndarray:
    """
    Build diagonal mass matrix from list of inertias [I1, I2, ...].
    """
    return np.diag(inertias)


def build_stiffness_matrix(k_terms: dict) -> np.ndarray:
    """
    Build stiffness matrix from dict of ((i,j): kij) entries for DOFs.
    Example: k_terms={(0,0):k1,(0,1):-k1,(1,1):k1+k2}
    """
    n = max(idx for pair in k_terms for idx in pair) + 1
    K = np.zeros((n, n))
    for (i, j), val in k_terms.items():
        K[i, j] = val
    return K


def build_damping_matrix(c_terms: dict) -> np.ndarray:
    """
    Build damping matrix similar to stiffness: c_terms dict of ((i,j): cij).
    """
    n = max(idx for pair in c_terms for idx in pair) + 1
    C = np.zeros((n, n))
    for (i, j), val in c_terms.items():
        C[i, j] = val
    return C


def lagrange_equations(T: np.ndarray, U: np.ndarray, C: np.ndarray, q: list) -> list:
    """
    Symbolically generate Lagrange equations: d/dt(dT/dq_dot) - dT/dq + dU/dq + dD/dq_dot = 0
    T, U, C should be sympy expressions; q list of symbols.
    Returns list of equations. Requires sympy.
    """
    import sympy as sp
    qd = [sp.diff(sym, sp.Symbol('t')) for sym in q]
    D = 1/2 * sum([C[i,i]*qd[i]**2 for i in range(len(q))])  # simplistic Rayleigh
    eqs = []
    for qi, qdi in zip(q, qd):
        dL_dqdot = sp.diff(T, qdi)
        d_dt = sp.diff(dL_dqdot, sp.Symbol('t'))
        dL_dq = sp.diff(T, qi)
        dU_dq = sp.diff(U, qi)
        dD_dqdot = sp.diff(D, qdi)
        eqs.append(d_dt - dL_dq + dU_dq + dD_dqdot)
    return eqs

# ----------------------------
# CRIB SHEET UPDATE
# ----------------------------
# Added:
# - transmission_angle
# - mobility_2d (Gruebler's equation)
# - build_mass_matrix, build_stiffness_matrix, build_damping_matrix
# - lagrange_equations (symbolic Lagrange eqns)

# Use cases:
# * mobility_2d for linkage DOF count
# * transmission_angle to assess mechanical advantage
# * matrix builders for MDOF assembly
# * lagrange_equations for deriving EOM symbolically

# Exceptions:
# - lagrange_equations requires sympy and time-symbols in expressions
# - matrix builders expect consistent DOF indexing