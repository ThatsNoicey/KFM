import numpy as np
import matplotlib.pyplot as plt

# Domain and spatial grid
L_total = 1.0e-3  # total length = 1 mm
Nx = 300
x = np.linspace(0, L_total, Nx)
dx = x[1] - x[0]

# Time
T = 300           # total time in seconds
Nt = 30000
dt = T / Nt

# Diffusion coefficients for each section
D1 = 2e-10   # lumen
D2 = 1e-10   # mucus
D3 = 0.5e-10 # epithelium
D4 = 0.1e-10 # deeper tissue

# Region boundaries (in m)
lumen_end = 0.2e-3
mucus_end = 0.3e-3
epi_end = 0.5e-3

# Enzyme degradation rate (1/s)
k_deg = 0.01  # adjust as needed

# Michaelis-Menten parameters for absorption (m/s)
Vmax = 1e-9   # maximal absorption rate (mol/m²/s)
Km = 0.2      # Michaelis constant (mol/m³) -- assuming normalized units

# Concentration initialization
C = np.zeros(Nx)
C[x <= lumen_end] = 1.0  # initial enzyme in lumen

# Diffusion profile
D_profile = np.zeros(Nx)
for i, xi in enumerate(x):
    if xi <= lumen_end:
        D_profile[i] = D1
    elif xi <= mucus_end:
        D_profile[i] = D2
    elif xi <= epi_end:
        D_profile[i] = D3
    else:
        D_profile[i] = D4

# Tracking absorption
absorbed_total = 0.0
absorbed_history = []

# Time evolution
for n in range(Nt):
    C_new = C.copy()
    
    for i in range(1, Nx - 1):
        D = D_profile[i]
        alpha = D * dt / dx**2
        diffusion_term = alpha * (C[i+1] - 2*C[i] + C[i-1])
        degradation_term = k_deg * C[i] * dt
        C_new[i] = max(0.0, C[i] + diffusion_term - degradation_term)

    # Michaelis-Menten absorption at boundary
    C_last = max(C[-1], 1e-12)
    absorption_rate = Vmax * C_last / (Km + C_last)
    delta_C = absorption_rate * dx * dt
    absorbed_amount = absorption_rate * dt  # mol/m²

    C_new[-1] = max(0.0, C[-2] - delta_C)
    C_new[0] = C_new[1]  # zero-flux boundary

    # Update
    C = C_new
    absorbed_total += absorbed_amount
    absorbed_history.append(absorbed_total)

# Plotting final concentration profile
plt.figure(figsize=(10, 4))
plt.subplot(1, 2, 1)
plt.plot(x * 1e3, C)
plt.xlabel("Position in gut wall (mm)")
plt.ylabel("Enzyme concentration")
plt.title("Final enzyme concentration")
plt.grid()

# Plotting absorption over time
time_array = np.linspace(0, T, Nt+1)[:Nt]
plt.subplot(1, 2, 2)
plt.plot(time_array, absorbed_history)
plt.xlabel("Time (s)")
plt.ylabel("Cumulative absorbed enzyme (mol/m²)")
plt.title("Total enzyme absorbed over time")
plt.grid()

plt.tight_layout()
plt.show()
